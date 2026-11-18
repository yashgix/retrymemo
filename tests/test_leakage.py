"""Records from the test half, the query itself, or later can never reach tuning or a query's file lists."""

import importlib.util
import unittest
from dataclasses import replace
from pathlib import Path

from recallbench.data import Session, Task, load
from recallbench.retrieve import Corpus
from recallbench.split import date_split
from recallbench.vote import Fix, vote_files

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("tune_under_test", ROOT / "tools" / "tune_vote.py")
tune_vote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tune_vote)

SMALL_REPOS = {"PyCQA/pyflakes", "Textualize/textual", "reframe-hpc/reframe"}


def mutate_task(task: Task) -> Task:
    return replace(task, issue="leak zebra parser crash", gold_files=("leak.py",))


def mutate_session(session: Session) -> Session:
    return replace(session, resolved=not session.resolved, opened=("leak.py",), edited=("leak.py",), turns=("leak",))


def mutate(tasks, sessions, indices):
    ids = {tasks[i].instance_id for i in indices}
    tasks = [mutate_task(t) if i in indices else t for i, t in enumerate(tasks)]
    return tasks, [mutate_session(s) if s.instance_id in ids else s for s in sessions]


def file_lists(tasks, sessions, q):
    corpus = Corpus(tasks, sessions)
    prior = corpus.past_fix_files(q)
    hybrid = [sessions[i] for i in corpus.rank("hybrid", q)]
    fixes = [Fix(tasks[j].gold_files) for j in corpus.issue_tasks(q)]
    return prior, vote_files(hybrid, prior), vote_files(fixes, prior)


class TuningTest(unittest.TestCase):
    def test_changing_every_test_half_task_and_session_leaves_tuning_unchanged(self):
        tasks, sessions = load()
        keep = [t for t in tasks if t.repo in SMALL_REPOS]
        ids = {t.instance_id for t in keep}
        kept_sessions = [s for s in sessions if s.instance_id in ids]
        _, test = date_split(keep, Corpus(keep, kept_sessions).queries())
        self.assertTrue(test)
        dev = set(range(len(keep))) - set(test)
        before = tune_vote.tune(keep, kept_sessions)
        self.assertEqual(tune_vote.tune(*mutate(keep, kept_sessions, set(test))), before)
        # Control: the same change to the other tasks does move the tuning, so the check can fail.
        self.assertNotEqual(tune_vote.tune(*mutate(keep, kept_sessions, dev)), before)


class QueryTest(unittest.TestCase):
    def test_current_equal_time_and_future_records_do_not_change_a_query_lists(self):
        day = "2024-01-{:02d} 00:00:00+00:00"
        tasks = [
            Task(f"t-{i}", "o/a", day.format(i + 1), "MIT", f"parser crash {i}", (f"f{i % 3}.py",)) for i in range(6)
        ]
        tasks.append(Task("t-q", "o/a", day.format(7), "MIT", "parser crash", ("q.py",)))
        tasks.append(Task("t-same", "o/a", day.format(7), "MIT", "parser crash", ("same.py",)))
        tasks.append(Task("t-later", "o/a", day.format(8), "MIT", "parser crash", ("later.py",)))
        sessions = [
            Session(t.instance_id, i, "m", True, "submitted", (), t.gold_files, ()) for i, t in enumerate(tasks)
        ]
        q = 6
        before = file_lists(tasks, sessions, q)
        self.assertEqual(file_lists(*mutate(tasks, sessions, {6, 7, 8}), q), before)
        self.assertNotIn("leak.py", {path for files in before for path in files})
