"""v5: the handoff text in the agent's first message per arm (A none, B v4 files, C v4 files plus a note)."""

import re

from recallbench.handoff import created

ARMS = ("A", "B", "C")
TOP_FILES = 5
PATCH_LINES = 60
NEW_FILE = re.compile(r"(?m)^new file mode ")


def files_text(files: list[str]) -> str:
    if not files:
        return "An earlier attempt at this issue did not fix it. It points to no file."
    lines = [f"{i}. {path}" for i, path in enumerate(files[:TOP_FILES], 1)]
    return (
        "An earlier attempt at this issue did not fix it. These files were the most relevant to that attempt, most "
        "relevant first. They may help you start, but they may not be the right files.\n" + "\n".join(lines)
    )


def reproduction(attempt: dict) -> tuple[str, str]:
    """The last command that ran a script the attempt created itself, and the error it hit ("" if none)."""
    own = created(attempt)
    runs = [
        (cmd, err) for cmd, err in attempt["commands"] if cmd.split()[:1] == ["python"] and own & set(cmd.split()[1:2])
    ]
    return runs[-1] if runs else ("", "")


def edits_only(patch: str) -> list[str]:
    """Patch lines without the files the attempt created (its scratch scripts), like `edited` in v4."""
    sections = re.split(r"(?m)^(?=diff --git )", patch.strip())
    return [line for s in sections if s and not NEW_FILE.search(s) for line in s.splitlines()]


def note_text(attempt: dict) -> str:
    edited = ", ".join(attempt["edited"]) or "none"
    patch = edits_only(attempt["patch"])
    parts = [f"Files it edited: {edited}"]
    if patch:
        cut = "\n".join(patch[:PATCH_LINES])
        parts.append(f"Its patch (first {PATCH_LINES} lines). This patch did not fix the issue:\n```diff\n{cut}\n```")
    else:
        parts.append("It left no patch to existing files.")
    command, error = reproduction(attempt)
    if command:
        parts.append(f"Reproduction command it ran: `{command}`. Last error from it: {error or 'none recorded'}")
    return "\n".join(parts)


def arm_text(arm: str, files: list[str], attempt: dict) -> str:
    if arm == "A":
        return ""
    body = files_text(files) if arm == "B" else files_text(files) + "\n" + note_text(attempt)
    return f"<previous_attempt>\n{body}\n</previous_attempt>"
