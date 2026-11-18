"""One set of redaction rules for everything a paid run would send: the memory texts and the agent's working copy.

The source folders carry private names and their docs hold private notes. Names become public project names, home
paths go, and any paragraph or bullet with private wording is dropped as a whole, so no fragment is left behind.

The generic patterns (emails, keys, tokens, home paths) are here. The private terms (folder names and their public
names, the owner's names and emails, private wording) are read from the git-ignored JSON file REPLAY_PRIVATE_TERMS
(default replay/private_terms.json), with the keys that `rules` reads. Without it only the generic patterns apply.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from replay import ROOT, SOURCES

TERMS = Path(os.environ.get("REPLAY_PRIVATE_TERMS", ROOT / "private_terms.json"))
NEVER = re.compile(r"(?!)")
SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}"
    r"|xox[abprs]-[\w-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=]{40,}"
)
EMAIL = re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
TEST_DOMAIN = re.compile(r"\.(test|example|invalid|local)$")


def any_of(parts: list[str], flags: int = re.I) -> re.Pattern:
    return re.compile("|".join(parts), flags) if parts else NEVER


@dataclass(frozen=True)
class Rules:
    public: dict[str, str]  # distinctive names, replaced everywhere
    in_paths: dict[str, str]  # common words (and open-source subjects), replaced only as a project folder
    replace: dict[str, str]  # other words replaced everywhere, such as the owner's handle and name
    owner_emails: frozenset[str]  # as sha256 of the lower-case address
    strip: re.Pattern  # removed inline from prose
    drop: re.Pattern  # a prose block with any of it is dropped
    drop_section: re.Pattern  # a prose section with this heading is dropped
    private: re.Pattern  # the smoke test and the paid run refuse a payload with any of it
    flags: re.Pattern  # private plus words that are usually harmless here, for review by eye
    blocked: re.Pattern  # the request boundary: no request may carry any of it
    loose: str  # a grep -E first pass for the boundary inside the container
    targeted: tuple[tuple[re.Pattern, str, str], ...]  # (file path, old, new) replacements, data files included
    reviewer: str  # the word that marks a review record's file name


def rules(terms: dict) -> Rules:
    public = terms.get("public", {})
    names = "|".join(re.escape(n) for n in public)
    private = [r"/Users/", *terms.get("private", []), *([rf"\b({names})\b"] if names else [])]
    sections = "|".join(terms.get("drop_sections", []))
    return Rules(
        public=public,
        in_paths=terms.get("in_paths", {}),
        replace=terms.get("replace", {}),
        owner_emails=frozenset(terms.get("owner_emails", [])),
        strip=any_of(terms.get("strip", [])),
        drop=any_of([*terms.get("drop", []), *([rf"https?://[^\s)]*({names})"] if names else [])]),
        drop_section=any_of([rf"^#+ .*({sections})\s*$"] if sections else [], re.I | re.M),
        private=any_of(private),
        flags=any_of([*private, r"~/", r"@[a-z0-9-]+\.[a-z]{2,}", *terms.get("flags", [])]),
        blocked=any_of(terms.get("blocked", [])),
        loose=terms.get("loose", ""),
        targeted=tuple((re.compile(p), old, new) for p, old, new in terms.get("targeted", [])),
        reviewer=terms.get("reviewer", ""),
    )


def load(path: Path = TERMS) -> Rules:
    return rules(json.loads(path.read_text()) if path.exists() else {})


RULES = load()


def project_of(path: Path) -> str:
    folder = path.relative_to(SOURCES).parts[0]
    return RULES.public.get(folder) or RULES.in_paths.get(folder) or folder


def source_path(public: str) -> str:
    """A task.json path, stored with the public project name, back to the folder and file names on disk."""
    folder, _, rest = public.partition("/")
    folder = next((p for p, n in {**RULES.in_paths, **RULES.public}.items() if n == folder), folder)
    if RULES.reviewer:
        rest = re.sub(r"\breviewer\b", RULES.reviewer, rest)
    return f"{folder}/{rest}"


def names(text: str) -> str:
    """Paths, names and the owner. Safe for code: it never removes a line or touches test data like emails."""
    if SECRET.search(text):
        raise ValueError("text holds something that looks like a secret: stop and check it by hand")
    root = re.escape(str(SOURCES))
    text = re.sub(rf"{root}/([\w.-]+)/repo/", "", text)
    text = re.sub(rf"{root}/([\w.-]+)/", lambda m: project_of(SOURCES / m.group(1)) + "/", text)
    text = re.sub(r"/Users/[^/\s]+/", "~/", text)
    text = re.sub(r"/private/tmp/\S*", "<tmp>", text)
    for name, public in RULES.public.items():
        text = re.sub(rf"\b{re.escape(name)}\b", public, text, flags=re.I)
    for name, public in RULES.in_paths.items():
        text = re.sub(rf"(?<![\w-]){re.escape(name)}/(?=records|repo)", f"{public}/", text)
    for word, neutral in RULES.replace.items():
        text = re.sub(rf"\b{re.escape(word)}\b", neutral, text, flags=re.I)
    return text


def blocks(text: str) -> list[str]:
    """Paragraphs and list items, each with its continuation lines."""
    out: list[str] = []
    previous = ""
    for line in text.split("\n"):
        item = re.match(r"([-*|#>]|\d+\.)\s", line)
        if not out or not line.strip() or not previous.strip() or item or previous.startswith("#"):
            out.append(line)
        else:
            out[-1] += "\n" + line
        previous = line
    return out


def without_sections(text: str) -> str:
    """Drop each matching section, from its heading to the next heading of the same or a higher level."""
    for match in reversed(list(RULES.drop_section.finditer(text))):
        level = len(match.group(0)) - len(match.group(0).lstrip("#"))
        nxt = re.compile(rf"^#{{1,{level}}} ", re.M).search(text, match.end())
        text = text[: match.start()] + (text[nxt.start() :] if nxt else "")
    return text


def prose(text: str) -> str:
    """For documents and review records: names, then drop sections and blocks with private wording."""
    text = EMAIL.sub(lambda m: m.group(0) if TEST_DOMAIN.search(m.group(0)) else "<email>", names(text))
    text = without_sections(RULES.strip.sub("", text))
    return "\n".join(b for b in blocks(text) if not RULES.drop.search(b))


def private(text: str) -> list[str]:
    return [m.group(0) for m in RULES.private.finditer(text)]


def flags(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in RULES.flags.finditer(text)})


def blocked(text: str) -> list[str]:
    """What in this text must not be sent: a blocked term, the owner's email, or the API key itself."""
    found = [m.group(0) for m in RULES.blocked.finditer(text)]
    found += [
        "the owner's email"
        for m in EMAIL.finditer(text)
        if hashlib.sha256(m.group(0).lower().encode()).hexdigest() in RULES.owner_emails
    ]
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    return found + (["the API key"] if len(key) > 8 and key in text else [])


def strings(value) -> list[str]:
    """Every string in a request, keys included: message contents, tool calls and their arguments, tool results."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [*strings(k), *strings(v)]]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in strings(v)]
    return []


def request_problems(*payload) -> list[str]:
    return sorted({hit for text in strings(list(payload)) for hit in blocked(text)})
