import json
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Task:
    instance_id: str
    repo: str
    created_at: str
    license: str
    issue: str
    gold_files: tuple[str, ...]


@dataclass(frozen=True)
class Session:
    instance_id: str
    row: int
    model: str
    resolved: bool
    exit_status: str
    opened: tuple[str, ...]
    edited: tuple[str, ...]
    turns: tuple[str, ...]

    @property
    def pointers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.edited + self.opened))


def _rows(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def load(data_dir: str | Path = DATA_DIR) -> tuple[list[Task], list[Session]]:
    root = Path(data_dir)
    tasks = [
        Task(
            row["instance_id"],
            row["repo"],
            row["created_at"],
            row["license"],
            row["issue"],
            tuple(row["gold_files"]),
        )
        for row in _rows(root / "tasks.jsonl")
    ]
    known = {task.instance_id for task in tasks}
    sessions = []
    for row in _rows(root / "sessions.jsonl"):
        if row["instance_id"] not in known:
            raise ValueError(f"session {row['instance_id']} has no task")
        sessions.append(
            Session(
                row["instance_id"],
                row["row"],
                row["model"],
                row["resolved"],
                row["exit_status"],
                tuple(row["opened"]),
                tuple(row["edited"]),
                tuple(row["turns"]),
            )
        )
    return tasks, sessions
