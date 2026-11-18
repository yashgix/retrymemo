import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from recallbench.data import DATA_DIR, Session, Task, load

TASK_ROWS = [
    {
        "instance_id": "org__repo-2",
        "repo": "org/repo",
        "created_at": "2024-02-01 00:00:00+00:00",
        "license": "MIT",
        "issue": "second",
        "gold_files": ["b.py", "a.py"],
    },
    {
        "instance_id": "org__repo-1",
        "repo": "org/repo",
        "created_at": "2024-01-01 00:00:00+00:00",
        "license": "BSD-3-Clause",
        "issue": "first",
        "gold_files": ["a.py"],
    },
]

SESSION_ROWS = [
    {
        "instance_id": "org__repo-2",
        "row": 7,
        "model": "swe-agent-llama-70b",
        "resolved": False,
        "exit_status": "exit_context",
        "opened": ["pkg/b.py", "pkg/a.py"],
        "edited": ["pkg/a.py"],
        "turns": ["look at it", "apply the patch"],
    },
    {
        "instance_id": "org__repo-1",
        "row": 3,
        "model": "swe-agent-llama-8b",
        "resolved": True,
        "exit_status": "submitted",
        "opened": ["pkg/a.py"],
        "edited": [],
        "turns": [],
    },
]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class LoadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        write_jsonl(self.dir / "tasks.jsonl", TASK_ROWS)
        write_jsonl(self.dir / "sessions.jsonl", SESSION_ROWS)

    def test_tasks_keep_file_order_and_lists_become_tuples(self):
        tasks, _ = load(self.dir)
        self.assertEqual([t.instance_id for t in tasks], ["org__repo-2", "org__repo-1"])
        task = tasks[0]
        self.assertIsInstance(task, Task)
        self.assertEqual(task.repo, "org/repo")
        self.assertEqual(task.created_at, "2024-02-01 00:00:00+00:00")
        self.assertEqual(task.license, "MIT")
        self.assertEqual(task.issue, "second")
        self.assertEqual(task.gold_files, ("b.py", "a.py"))
        self.assertIsInstance(task.gold_files, tuple)

    def test_sessions_keep_file_order_with_plain_values(self):
        _, sessions = load(self.dir)
        self.assertEqual([s.instance_id for s in sessions], ["org__repo-2", "org__repo-1"])
        session = sessions[0]
        self.assertIsInstance(session, Session)
        self.assertEqual(session.row, 7)
        self.assertIsInstance(session.row, int)
        self.assertEqual(session.model, "swe-agent-llama-70b")
        self.assertIs(session.resolved, False)
        self.assertEqual(session.exit_status, "exit_context")
        self.assertEqual(session.opened, ("pkg/b.py", "pkg/a.py"))
        self.assertEqual(session.edited, ("pkg/a.py",))
        self.assertEqual(session.turns, ("look at it", "apply the patch"))
        self.assertIs(sessions[1].resolved, True)

    def test_accepts_a_str_or_a_path(self):
        self.assertEqual(load(str(self.dir)), load(self.dir))

    def test_session_without_a_task_is_an_error(self):
        extra = dict(SESSION_ROWS[0], instance_id="org__repo-9")
        write_jsonl(self.dir / "sessions.jsonl", [*SESSION_ROWS, extra])
        with self.assertRaises(ValueError):
            load(self.dir)

    def test_pointers_list_edited_files_first_without_duplicates(self):
        _, sessions = load(self.dir)
        self.assertEqual(sessions[0].pointers, ("pkg/a.py", "pkg/b.py"))
        self.assertEqual(sessions[1].pointers, ("pkg/a.py",))


@unittest.skipUnless((DATA_DIR / "tasks.jsonl").is_file(), "pinned data is not present")
class PinnedDataTest(unittest.TestCase):
    def test_counts(self):
        tasks, sessions = load()
        self.assertEqual((len(tasks), len(sessions)), (677, 812))
        self.assertEqual(sum(session.resolved for session in sessions), 138)
        self.assertEqual(len({task.repo for task in tasks}), 8)
        self.assertEqual(sum(1 for session in sessions if not session.pointers), 68)
        self.assertTrue(all(task.gold_files for task in tasks))
        known = {task.instance_id for task in tasks}
        self.assertTrue(all(session.instance_id in known for session in sessions))


class FrozenTest(unittest.TestCase):
    def test_tasks_and_sessions_reject_field_assignment(self):
        task = Task("t", "o/r", "2024-01-01 00:00:00+00:00", "MIT", "issue", ("a.py",))
        session = Session("t", 0, "m", True, "submitted", (), ("a.py",), ())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            task.issue = "changed"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            session.resolved = False
