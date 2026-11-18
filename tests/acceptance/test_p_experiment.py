import contextlib
import dataclasses
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

import recallbench
from recallbench.data import Session, Task, load
from recallbench.evaluate import hit_at
from recallbench.retrieve import RETRIEVERS, Corpus
from recallbench.run import BOOTSTRAP_DRAWS, SEED, main, metrics

ROOT = Path(recallbench.__file__).resolve().parent.parent
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
SCALARS = (
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
)
LISTS = {"full_budget_hit": 42, "card_budget_hit": 42, "repo_queries": 8, "repo_answerable": 8, "repo_hit5": 56}
HIT5 = (0.373534, 0.489112, 0.552764, 0.59129, 0.641541, 0.472362, 0.407035)
REPOS = [
    "PyCQA/pyflakes",
    "Textualize/textual",
    "asottile/pyupgrade",
    "iterative/dvc",
    "oasis-open/cti-python-stix2",
    "pydantic/pydantic",
    "reframe-hpc/reframe",
    "tobymao/sqlglot",
]


def load_demo():
    spec = importlib.util.spec_from_file_location("demo", ROOT / "demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    return demo


def pct(v):
    return f"{100 * v:.1f}%"


RUN = tempfile.TemporaryDirectory()


def run_once():
    """One experiment run shared by the test classes: (results folder, metrics file text)."""
    out = Path(RUN.name) / "deep" / "er" / "metrics.json"
    if not out.exists():
        main(["--out", str(out)])
    return out.parent, out.read_text()


class Experiment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        _, cls.raw = run_once()
        cls.m = json.loads(cls.raw)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_keys_and_format(self):
        self.assertEqual(set(self.m), set(PER_RETRIEVER) | set(SCALARS) | set(LISTS))
        self.assertEqual(len(self.m), 31)
        self.assertEqual(self.raw, json.dumps(self.m, sort_keys=True, indent=1) + "\n")
        for key in PER_RETRIEVER:
            self.assertEqual(len(self.m[key]), 7, key)
        for key, n in LISTS.items():
            self.assertEqual(len(self.m[key]), n, key)
        for value in [v for k in PER_RETRIEVER for v in self.m[k]] + self.m["repo_hit5"]:
            self.assertEqual(value, round(value, 6))

    def test_counts(self):
        m = self.m
        got = {k: m[k] for k in ("tasks", "sessions", "resolved_sessions", "repos", "queries", "answerable")}
        self.assertEqual(
            got,
            {"tasks": 677, "sessions": 812, "resolved_sessions": 138, "repos": 8, "queries": 597, "answerable": 510},
        )
        self.assertEqual(
            (m["min_history"], m["bootstrap_draws"], m["budgets"]), (10, 1000, [500, 1000, 2000, 4000, 8000, 16000])
        )
        self.assertTrue(all(type(m[k]) is int for k in ("tasks", "queries", "answerable", "best_retriever")))
        self.assertEqual(m["repo_queries"], [10, 13, 45, 248, 13, 190, 10, 68])
        self.assertEqual(m["repo_answerable"], [10, 5, 37, 195, 8, 183, 8, 64])
        self.assertAlmostEqual(m["mean_pool"], 114.41876, places=5)
        self.assertAlmostEqual(m["mean_full_tokens"], 3712.899015, places=5)
        self.assertAlmostEqual(m["mean_card_tokens"], 36.742611, places=5)

    def test_results(self):
        m = self.m
        for name, got, want in zip(RETRIEVERS, m["hit5"], HIT5, strict=True):
            self.assertAlmostEqual(got, want, delta=0.005, msg=name)
        self.assertEqual(m["best_retriever"], 4)
        self.assertAlmostEqual(m["card_budget_hit"][4 * 6], 0.721943, delta=0.01)
        self.assertAlmostEqual(m["full_budget_hit"][4 * 6], 0.01675, delta=0.01)
        for i in range(7):
            self.assertLessEqual(m["hit5_lo"][i], m["hit5"][i])
            self.assertLessEqual(m["hit5"][i], m["hit5_hi"][i])
            self.assertLessEqual(m["hit1"][i], m["hit5"][i])
            ratio = m["full_tokens5"][i] / m["hit5"][i]
            self.assertAlmostEqual(m["full_tokens_per_hit"][i], ratio, delta=1e-4 * ratio)
            for form in ("full", "card"):
                row = m[f"{form}_budget_hit"][i * 6 : i * 6 + 6]
                self.assertEqual(row, sorted(row), (form, i))
        self.assertEqual([m[k][1] for k in ("hit5_vs_issue", "hit5_vs_issue_lo", "hit5_vs_issue_hi")], [0.0] * 3)
        self.assertLess(m["card_tokens5"][4], m["full_tokens5"][4] / 20)

    def test_bootstrap_and_recent_exactly(self):
        tasks, sessions = load(ROOT / "data")
        corpus = Corpus(tasks, sessions)
        queries = corpus.queries()

        def hits(name):
            return np.array(
                [hit_at([sessions[i].pointers for i in corpus.rank(name, q)], tasks[q].gold_files, 5) for q in queries],
                dtype=float,
            )

        recent, issue = hits("recent"), hits("bm25_issue")
        draws = np.random.default_rng(SEED).integers(0, len(queries), (BOOTSTRAP_DRAWS, len(queries)))
        means = np.sort(recent[draws].mean(axis=1))
        diff = np.sort((recent - issue)[draws].mean(axis=1))
        m = self.m
        self.assertEqual((SEED, BOOTSTRAP_DRAWS), (20261114, 1000))
        self.assertAlmostEqual(m["hit5"][0], round(recent.mean(), 6), places=6)
        self.assertAlmostEqual(m["hit5_lo"][0], round(means[25], 6), places=6)
        self.assertAlmostEqual(m["hit5_hi"][0], round(means[974], 6), places=6)
        self.assertAlmostEqual(m["hit5_vs_issue_lo"][0], round(diff[25], 6), places=6)
        self.assertAlmostEqual(m["hit5_vs_issue_hi"][0], round(diff[974], 6), places=6)
        dvc = [j for j, q in enumerate(queries) if tasks[q].repo == "iterative/dvc"]
        self.assertAlmostEqual(m["repo_hit5"][3], round(recent[dvc].mean(), 6), places=6)

    def test_script_on_small_data(self):
        tasks, sessions = small_corpus()
        data = Path(self.tmp.name) / "small"
        data.mkdir()
        for name, rows in (("tasks.jsonl", tasks), ("sessions.jsonl", sessions)):
            (data / name).write_text("".join(json.dumps(dataclasses.asdict(r)) + "\n" for r in rows))
        out = Path(self.tmp.name) / "small-out" / "metrics.json"
        command = [sys.executable, "experiment.py", "--out", str(out), "--data", str(data)]
        subprocess.run(command, cwd=ROOT, check=True, timeout=120)
        expected = json.dumps(metrics(*load(data)), sort_keys=True, indent=1) + "\n"
        self.assertEqual(out.read_text(), expected)

    def test_missing_data_exits(self):
        with tempfile.TemporaryDirectory() as empty, self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stderr(io.StringIO()):
                main(["--out", str(Path(empty) / "m.json"), "--data", empty])
        self.assertNotEqual(caught.exception.code, 0)


def small_corpus():
    """Two repos: 12 tasks in o/a (two of them are queries) and 3 in o/b (none)."""
    tasks = [
        Task(f"a-{i}", "o/a", f"2020-01-{i + 1:02d} 00:00:00+00:00", "mit", f"issue {i}", (f"m{i % 2}.py",))
        for i in range(12)
    ]
    tasks += [Task(f"b-{i}", "o/b", f"2020-01-{i + 1:02d} 00:00:00+00:00", "mit", "x", ("z.py",)) for i in range(3)]
    sessions = [
        Session(t.instance_id, i, "m", i % 3 == 0, "submitted", (), t.gold_files, ("x" * 40,))
        for i, t in enumerate(tasks)
    ]
    return tasks, sessions


class SyntheticMetrics(unittest.TestCase):
    def test_small_corpus(self):
        m = metrics(*small_corpus())
        self.assertEqual(len(m), 31)
        self.assertEqual(
            (m["queries"], m["repos"], m["answerable"], m["tasks"], m["resolved_sessions"]), (2, 2, 2, 15, 5)
        )
        self.assertEqual((m["hit1"][0], m["hit5"][0], m["hit5_lo"][0], m["hit5_hi"][0]), (0.0, 1.0, 1.0, 1.0))
        self.assertEqual(m["full_tokens5"][0], 60.5)
        self.assertEqual(m["mean_pool"], 10.5)
        self.assertEqual(m["repo_queries"], [2, 0])
        self.assertEqual([m["repo_hit5"][r * 2 + 1] for r in range(7)], [0.0] * 7)
        self.assertEqual(m["full_budget_hit"][:6], [1.0] * 6)


class Demo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.results, raw = run_once()
        cls.m = json.loads(raw)
        cls.demo = load_demo()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def render(self):
        template = Path(self.tmp.name) / "README.in.md"
        template.write_text(
            "# Title\n\nIntro text.\n\n<!-- results:start -->\nold block\n<!-- results:end -->\n\nTail.\n"
        )
        out = Path(self.tmp.name) / "README.md"
        self.demo.main(["--readme", str(template), "--out", str(out), "--results", str(self.results)])
        return out.read_text()

    def test_readme_block(self):
        text = self.render()
        self.assertTrue(text.startswith("# Title\n\nIntro text.\n\n<!-- results:start -->\n"))
        self.assertTrue(text.endswith("\n<!-- results:end -->\n\nTail.\n"))
        self.assertNotIn("old block", text)
        lines = text.splitlines()
        self.assertIn("|---|---|---|---|---|---|---|---|", lines)
        m = self.m
        for i, name in enumerate(RETRIEVERS):
            label = f"**`{name}`** (best)" if i == m["best_retriever"] else f"`{name}`"
            row = [line for line in lines if line.startswith(f"| {label} |")]
            self.assertEqual(len(row), 1, name)
            self.assertIn(f"{pct(m['hit5'][i])} ({pct(m['hit5_lo'][i])}-{pct(m['hit5_hi'][i])})", row[0])
            self.assertIn(f"{m['ndcg5'][i]:.3f}", row[0])
        for b in m["budgets"]:
            self.assertEqual(len([line for line in lines if line.startswith(f"| {b} |")]), 1, b)
        for j, repo in enumerate(REPOS):
            self.assertEqual(len([ln for ln in lines if ln.startswith(f"| {repo} | {m['repo_queries'][j]} |")]), 1)
        self.assertIn(f"{100 * m['hit5_vs_issue'][4]:+.1f}", text)

    def test_print_and_errors(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.demo.main(["--results", str(self.results)])
        line = buf.getvalue().strip()
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn(pct(self.m["hit5"][0]), line)
        self.assertIn(pct(self.m["hit5"][self.m["best_retriever"]]), line)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.demo.main(["--readme", "README.md", "--results", str(self.results)])
