import re

from .data import Session, Task

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    text = _CAMEL.sub(r"\1 \2", text).lower()
    return [term for term in _WORD.findall(text) if len(term) >= 2]


def tokens(text: str) -> int:
    return (len(text) + 3) // 4


def title(task: Task) -> str:
    issue = task.issue.strip()
    return issue.splitlines()[0].strip() if issue else ""


def session_text(task: Task, session: Session) -> str:
    return "\n".join((task.issue, *session.turns))


def card(task: Task, session: Session) -> str:
    return "\n".join(
        (
            title(task),
            "resolved: yes" if session.resolved else "resolved: no",
            "edited: " + ", ".join(session.edited),
            "opened: " + ", ".join(session.opened),
        )
    )


def costs(tasks: list[Task], sessions: list[Session]) -> tuple[list[int], list[int]]:
    """Token cost of each session as full text and as a card, each with its own past task."""
    by_id = {task.instance_id: task for task in tasks}
    own = [by_id[session.instance_id] for session in sessions]
    full = [tokens(session_text(task, session)) for task, session in zip(own, sessions, strict=True)]
    cards = [tokens(card(task, session)) for task, session in zip(own, sessions, strict=True)]
    return full, cards
