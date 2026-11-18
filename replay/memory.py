"""The five memory arms. The corpus is every second-model review record in the agent-built projects that existed
before the task's cutoff (by birth time and ctime, which normal tools cannot set back). Every text is sanitized first.

Arms: none, instructions (the builder brief of 16 Nov, read from REPLAY_INSTRUCTIONS), retrieved (BM25 on the task
text), oracle (the hand-picked lessons, padded with random records) and random (records drawn at random, never a
hand-picked one). The last three are cut to one character budget, so they differ in what they say, not in how much.
"""

import os
import random
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from recallbench.bm25 import bm25
from recallbench.text import words
from replay import SOURCES, redact
from replay.redact import EMAIL, TEST_DOMAIN, private, project_of, prose

EST = ZoneInfo("America/New_York")
BUDGET_CHARS = 18_000
INSTRUCTIONS = Path(os.environ.get("REPLAY_INSTRUCTIONS", SOURCES / "BUILDER-BRIEF.md"))
HEADER = "Notes from earlier code reviews in other projects and this one. They may or may not be relevant.\n\n"


@dataclass
class Record:
    key: str  # sanitized "<public project>/records/..." path
    created: datetime
    text: str


def born(path: Path) -> datetime:
    st = path.stat()
    return datetime.fromtimestamp(max(st.st_birthtime, st.st_ctime), EST)


def corpus(task: dict) -> list[Record]:
    """Review reports (not prompts or builder responses) from before the cutoff, minus the excluded ones."""
    records = []
    for path in sorted(SOURCES.glob("*/records/**/*.md")):
        rel = str(path.relative_to(SOURCES))
        name = path.name.lower()
        review = "review" in name and redact.RULES.reviewer in name and "prompt" not in name and "response" not in name
        if not review or rel in task["exclude_memory"]:
            continue
        if born(path) < task["cutoff_at"]:
            key = project_of(path) + "/" + str(Path(*path.relative_to(SOURCES).parts[1:]))
            records.append(Record(key, born(path), prose(path.read_text(errors="replace"))))
    return records


def gold_keys(task: dict) -> list[str]:
    return [project_of(SOURCES / g) + "/" + str(Path(*Path(g).parts[1:])) for g in task["gold_memory"]]


def item(record: Record) -> str:
    return f"### {record.key} (written {record.created:%Y-%m-%d %H:%M} EST)\n{record.text.strip()}\n\n"


def fill(records: list[Record], budget: int = BUDGET_CHARS) -> str:
    """Whole records in order until the budget, then the next one cut at a line so the total stays within it."""
    out = HEADER
    for record in records:
        text = item(record)
        if len(out) + len(text) <= budget:
            out += text
            continue
        room = budget - len(out) - len("[...]\n")
        cut = text[:room].rsplit("\n", 1)[0]
        return out + cut + "\n[...]\n" if "\n" in cut else out  # never end on a bare header
    return out


def ranked(records: list[Record], query: str) -> list[Record]:
    scores = bm25(words(query), [Counter(words(r.text)) for r in records])
    return [r for _, r in sorted(zip(scores, records, strict=True), key=lambda x: -x[0])]


def arm_text(task: dict, arm: str, seed: int, records: list[Record]) -> str:
    gold = gold_keys(task)
    picked = [r for r in records if r.key in gold]
    others = [r for r in records if r.key not in gold]
    shuffled = random.Random(f"{task['name']}:{arm}:{seed}").sample(others, len(others))
    if arm == "none":
        return ""
    if arm == "instructions":
        return "Team instructions for builders.\n\n" + prose(INSTRUCTIONS.read_text())
    if arm == "retrieved":
        return fill(ranked(records, task["text"]))
    if arm == "oracle":
        return fill(sorted(picked, key=lambda r: gold.index(r.key)) + shuffled)
    if arm == "random":
        return fill(shuffled)
    raise ValueError(arm)


def solution_lines(task: dict) -> set[str]:
    """Code lines the real fix added (tests and task inputs left out), long enough to be specific."""
    src = str(task["source_path"])
    skip = [f":(exclude){p}" for p in [*task["tests"], *task["inputs"], "*.test.ts", "*.md", "*.json", "*.lock"]]
    diff = subprocess.run(
        ["git", "-C", src, "diff", task["pre"], task["reference"], "--", ".", *skip],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    added = (line[1:].strip() for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    return {line for line in added if len(line) >= 30 and re.search(r"[A-Za-z]{3}", line)}


def test_titles(task: dict) -> set[str]:
    titles = set()
    for rel in task["tests"]:
        text = (task["dir"] / "tests" / rel).read_text()
        titles |= {m.group(3) for m in re.finditer(r"""\b(it|test)(?:\.each\([^)]*\))?\(\s*(['"])(.{12,}?)\2""", text)}
    return titles


def future_hashes(task: dict) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(task["source_path"]), "rev-list", "--all", f"^{task['pre']}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {h[:7] for h in out.split()}


def leaks(task: dict, text: str, checks: tuple[set, set, set]) -> list[str]:
    lines, titles, hashes = checks
    found = [f"solution line: {line[:60]}" for line in lines if line in text]
    found += [f"withheld test name: {t}" for t in titles if t in text]
    found += [f"future commit: {h}" for h in hashes if re.search(rf"\b{h}", text)]
    return found


def payload_problems(task: dict, payload: str) -> list[str]:
    """Why a payload must not be sent: private text, a real email, or a leak of this task's answer."""
    found = [f"private text: {hit}" for hit in private(payload)]
    found += [f"email: {m.group(0)}" for m in EMAIL.finditer(payload) if not TEST_DOMAIN.search(m.group(0))]
    return found + leaks(task, payload, (solution_lines(task), test_titles(task), future_hashes(task)))
