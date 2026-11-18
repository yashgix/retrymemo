"""Task specs (`tasks/<name>/task.json`) and how their withheld tests split into task and memory tests.

task.json stores source and record paths with public project names. Loading maps them back to the names on disk.
"""

import json
import re
from datetime import datetime

from replay import SOURCES, TASKS_DIR
from replay.redact import source_path

NAMES = ("lock", "verifier", "simlink", "onnx", "fczero")


def load(name: str) -> dict:
    task = json.loads((TASKS_DIR / name / "task.json").read_text())
    task["dir"] = TASKS_DIR / name
    task["source_path"] = SOURCES / source_path(task["source"])
    task["gold_memory"] = [source_path(p) for p in task["gold_memory"]]
    task["exclude_memory"] = {source_path(p): why for p, why in task["exclude_memory"].items()}
    task["cutoff_at"] = datetime.fromisoformat(task["cutoff"])
    if task["cutoff_at"].tzinfo is None:
        raise ValueError(f"{name}: cutoff needs a time zone")
    return task


def patch(task: dict, kind: str) -> bytes:
    return b"" if kind == "pre" else (task["dir"] / "patches" / f"{kind}.patch").read_bytes()


def withheld(task: dict, test_id: str) -> bool:
    return test_id.split(" > ", 1)[0] in task["tests"]


def is_memory_test(task: dict, test_id: str) -> bool:
    name = test_id.split(" > ", 1)[1]
    return withheld(task, test_id) and any(re.search(p, name) for p in task["memory_tests"])
