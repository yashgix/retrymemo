"""Tests for recallbench.run: the metrics dictionary and the command line."""

import json
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import asdict
from io import StringIO
from pathlib import Path

from recallbench.data import Session, Task, load
from recallbench.evaluate import BUDGETS
from recallbench.files import file_metrics
from recallbench.retrieve import RETRIEVERS
from recallbench.run import BOOTSTRAP_DRAWS, FILES_NAME, main, metrics
from recallbench.text import card, session_text, tokens

FILES = ("pkg/a.py", "pkg/b.py", "pkg/c.py", "pkg/d.py")
PER_RETRIEVER = (
    "hit1",
    "hit5",
    "recall5",
    "ndcg5",
    "full_tokens5",
    "card_tokens5",
    "hit5_lo",
    "hit5_hi",
    "hit5_vs_issue",
    "hit5_vs_issue_lo",
    "hit5_vs_issue_hi",
    "full_tokens_per_hit",
    "card_tokens_per_hit",
)
KEYS = {
    "tasks",
    "sessions",
    "resolved_sessions",
    "repos",
    "queries",
    "answerable",
    "min_history",
    "bootstrap_draws",
    "mean_pool",
    "mean_full_tokens",
    "mean_card_tokens",
    "best_retriever",
    "budgets",
    "full_budget_hit",
    "card_budget_hit",
    "repo_queries",
    "repo_answerable",
    "repo_hit5",
    *PER_RETRIEVER,
}


def make_dataset():
    """Twelve org/a tasks (two queries, one of them answerable) and three org/b tasks (no queries)."""
    tasks, sessions = [], []
    for i in range(12):
        tasks.append(
            Task(
                f"org__a-{i}",
                "org/a",
                f"2024-01-{i + 1:02d} 00:00:00+00:00",
                "MIT",
                f"issue {i} crashes on parse",
                (FILES[i % 4],),
            )
        )
        resolved = i % 2 == 0
        sessions.append(
            Session(
                f"org__a-{i}",
                i,
                "swe-agent-llama-70b",
                resolved,
                "submitted" if resolved else "exit_context",
                (FILES[i % 4],) if resolved else (),
                ("scratch/repro.py",) if i % 3 == 0 else (),
                (f"turn {i}",),
            )
        )
    for i in range(3):
        tasks.append(
            Task(
                f"org__b-{i}",
                "org/b",
                f"2024-02-{i + 1:02d} 00:00:00+00:00",
                "BSD-3-Clause",
                "issue b",
                (FILES[i],),
            )
        )
        sessions.append(
            Session(
                f"org__b-{i}",
                12 + i,
                "swe-agent-llama-8b",
                True,
                "submitted",
                ("pkg/a.py",),
                (),
                ("turn b",),
            )
        )
    return tasks, sessions


class MetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks, cls.sessions = make_dataset()
        cls.m = metrics(cls.tasks, cls.sessions)

    def test_exact_key_set(self):
        self.assertEqual(set(self.m), KEYS)
        self.assertEqual(len(KEYS), 31)

    def test_dataset_counts(self):
        self.assertEqual(self.m["tasks"], 15)
        self.assertEqual(self.m["sessions"], 15)
        self.assertEqual(self.m["resolved_sessions"], 9)
        self.assertEqual(self.m["repos"], 2)
        self.assertEqual(self.m["queries"], 2)
        self.assertEqual(self.m["answerable"], 1)
        self.assertEqual(self.m["min_history"], 10)
        self.assertEqual(self.m["bootstrap_draws"], BOOTSTRAP_DRAWS)
        self.assertEqual(self.m["bootstrap_draws"], 1000)
        self.assertEqual(self.m["budgets"], list(BUDGETS))
        self.assertEqual(self.m["budgets"], [500, 1000, 2000, 4000, 8000, 16000])

    def test_mean_pool(self):
        self.assertEqual(self.m["mean_pool"], 10.5)

    def test_mean_costs_use_each_session_own_task(self):
        own = {task.instance_id: task for task in self.tasks}
        full = [tokens(session_text(own[s.instance_id], s)) for s in self.sessions]
        cards = [tokens(card(own[s.instance_id], s)) for s in self.sessions]
        self.assertAlmostEqual(self.m["mean_full_tokens"], sum(full) / len(full), places=6)
        self.assertAlmostEqual(self.m["mean_card_tokens"], sum(cards) / len(cards), places=6)

    def test_per_retriever_lists_have_one_entry_per_retriever(self):
        for key in PER_RETRIEVER:
            with self.subTest(key=key):
                self.assertEqual(len(self.m[key]), len(RETRIEVERS))

    def test_hits_are_means_of_two_binary_queries(self):
        for key in ("hit1", "hit5", "hit5_lo", "hit5_hi"):
            for value in self.m[key]:
                self.assertIn(value, (0.0, 0.5, 1.0))

    def test_rank_measures_stay_between_zero_and_one(self):
        for key in ("recall5", "ndcg5"):
            for value in self.m[key]:
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_bootstrap_brackets_the_mean(self):
        for i, name in enumerate(RETRIEVERS):
            with self.subTest(retriever=name):
                self.assertLessEqual(self.m["hit5_lo"][i], self.m["hit5"][i])
                self.assertGreaterEqual(self.m["hit5_hi"][i], self.m["hit5"][i])

    def test_hit5_against_bm25_issue(self):
        base = RETRIEVERS.index("bm25_issue")
        self.assertEqual(self.m["hit5_vs_issue"][base], 0.0)
        self.assertEqual(self.m["hit5_vs_issue_lo"][base], 0.0)
        self.assertEqual(self.m["hit5_vs_issue_hi"][base], 0.0)
        for i, name in enumerate(RETRIEVERS):
            with self.subTest(retriever=name):
                difference = self.m["hit5"][i] - self.m["hit5"][base]
                self.assertAlmostEqual(self.m["hit5_vs_issue"][i], difference, places=6)
                self.assertLessEqual(self.m["hit5_vs_issue_lo"][i], self.m["hit5_vs_issue"][i])
                self.assertGreaterEqual(self.m["hit5_vs_issue_hi"][i], self.m["hit5_vs_issue"][i])

    def test_best_retriever_is_the_hit5_winner(self):
        best = self.m["best_retriever"]
        self.assertIsInstance(best, int)
        self.assertNotIsInstance(best, bool)
        self.assertEqual(self.m["hit5"][best], max(self.m["hit5"]))
        self.assertEqual(best, max(range(len(RETRIEVERS)), key=lambda i: (self.m["hit5"][i], -i)))

    def test_budget_lists_are_retriever_major(self):
        for key in ("full_budget_hit", "card_budget_hit"):
            values = self.m[key]
            self.assertEqual(len(values), len(RETRIEVERS) * len(BUDGETS))
            for value in values:
                self.assertIn(value, (0.0, 0.5, 1.0))
            last = len(BUDGETS) - 1
            for r, name in enumerate(RETRIEVERS):
                with self.subTest(key=key, retriever=name):
                    self.assertEqual(values[r * len(BUDGETS)], 0.5)
                    self.assertEqual(values[r * len(BUDGETS) + last], 0.5)

    def test_repository_lists(self):
        self.assertEqual(self.m["repo_queries"], [2, 0])
        self.assertEqual(self.m["repo_answerable"], [1, 0])
        self.assertEqual(len(self.m["repo_hit5"]), len(RETRIEVERS) * 2)
        for r, name in enumerate(RETRIEVERS):
            with self.subTest(retriever=name):
                self.assertEqual(self.m["repo_hit5"][r * 2], self.m["hit5"][r])
                self.assertEqual(self.m["repo_hit5"][r * 2 + 1], 0.0)

    def test_two_runs_give_the_same_metrics(self):
        self.assertEqual(metrics(self.tasks, self.sessions), self.m)


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class MainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        tasks, sessions = make_dataset()
        write_jsonl(self.data / "tasks.jsonl", [asdict(task) for task in tasks])
        write_jsonl(self.data / "sessions.jsonl", [asdict(session) for session in sessions])
        self.out = self.root / "results" / "metrics.json"

    def test_writes_the_metrics_as_json(self):
        main(["--out", str(self.out), "--data", str(self.data)])
        text = self.out.read_text(encoding="utf-8")
        data = json.loads(text)
        self.assertEqual(text, json.dumps(data, sort_keys=True, indent=1) + "\n")
        self.assertEqual(data, metrics(*load(self.data)))

    def test_creates_missing_parent_folders(self):
        self.assertFalse(self.out.parent.exists())
        main(["--out", str(self.out), "--data", str(self.data)])
        self.assertTrue(self.out.is_file())

    def test_two_runs_write_identical_files(self):
        other = self.root / "other.json"
        main(["--out", str(self.out), "--data", str(self.data)])
        main(["--out", str(other), "--data", str(self.data)])
        self.assertEqual(self.out.read_bytes(), other.read_bytes())

    def test_writes_the_file_level_metrics_beside_the_metrics(self):
        main(["--out", str(self.out), "--data", str(self.data)])
        text = (self.out.parent / FILES_NAME).read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(file_metrics(*load(self.data)), sort_keys=True, indent=1) + "\n")

    def test_missing_tasks_file_exits_non_zero(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(["--out", str(self.out), "--data", str(self.root / "missing")])
        self.assertNotEqual(caught.exception.code, 0)
        self.assertFalse(self.out.exists())
