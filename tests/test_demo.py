import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from recallbench.data import Session, Task
from recallbench.files import file_metrics
from recallbench.run import metrics

ROOT = Path(__file__).resolve().parent.parent


def _load_demo():
    spec = importlib.util.spec_from_file_location("demo_under_test", ROOT / "demo.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_demo()


REPOS = ["iterative/dvc", "pydantic/pydantic"]


def make_metrics():
    n = len(demo.RETRIEVERS)
    best = demo.RETRIEVERS.index("hybrid")
    issue = demo.RETRIEVERS.index("bm25_issue")
    budgets = [500, 1000, 2000, 4000, 8000, 16000]

    full_budget_hit = [(r + 1) / 10 + b / 100 for r in range(n) for b in range(len(budgets))]
    card_budget_hit = [(r + 2) / 10 + b / 100 for r in range(n) for b in range(len(budgets))]

    repo_hit5 = [0.0] * (n * 2)
    repo_hit5[0] = 0.169
    repo_hit5[issue * 2] = 0.262
    repo_hit5[best * 2] = 0.476

    full_tokens_per_hit = [1000.0] * n
    card_tokens_per_hit = [37.0] * n
    full_tokens_per_hit[best] = 8995.0
    card_tokens_per_hit[best] = 57.0

    full_tokens5 = [1000.0] * n
    card_tokens5 = [37.0] * n
    full_tokens5[best] = 5774.0
    card_tokens5[best] = 57.0

    return {
        "tasks": 677,
        "sessions": 812,
        "resolved_sessions": 138,
        "repos": 2,
        "queries": 597,
        "answerable": 510,
        "min_history": 10,
        "bootstrap_draws": 1000,
        "mean_pool": 15.2,
        "mean_full_tokens": 3700.0,
        "mean_card_tokens": 37.0,
        "best_retriever": best,
        "budgets": budgets,
        "hit1": [0.100, 0.200, 0.300, 0.400, 0.500, 0.450, 0.350],
        "hit5": [0.371, 0.488, 0.500, 0.520, 0.642, 0.470, 0.410],
        "hit5_lo": [0.340, 0.450, 0.465, 0.490, 0.605, 0.440, 0.380],
        "hit5_hi": [0.400, 0.520, 0.535, 0.550, 0.680, 0.500, 0.440],
        "recall5": [0.200, 0.300, 0.400, 0.500, 0.600, 0.550, 0.450],
        "ndcg5": [0.123, 0.234, 0.345, 0.456, 0.567, 0.678, 0.789],
        "hit5_vs_issue": [-0.117, 0.0, 0.012, 0.032, 0.152, -0.018, -0.078],
        "hit5_vs_issue_lo": [-0.151, 0.0, -0.020, 0.010, 0.117, -0.050, -0.110],
        "hit5_vs_issue_hi": [-0.080, 0.0, 0.050, 0.070, 0.191, 0.020, -0.040],
        "full_tokens5": full_tokens5,
        "card_tokens5": card_tokens5,
        "full_tokens_per_hit": full_tokens_per_hit,
        "card_tokens_per_hit": card_tokens_per_hit,
        "full_budget_hit": full_budget_hit,
        "card_budget_hit": card_budget_hit,
        "repo_queries": [248, 190],
        "repo_answerable": [195, 150],
        "repo_hit5": repo_hit5,
    }


def comparison(a, b, diff, query_ci, repo_ci):
    return {"metric": "file_hit@5", "a": a, "b": b, "diff": diff, "query_ci": query_ci, "repo_ci": repo_ci}


RANKERS = [*demo.RETRIEVERS, "most_files", "past_fix_files", "issue_fix_vote", "vote_prior"]


def make_half(queries, hit5):
    sessions = RANKERS[:-3]
    file_hit = {name: [0.1, hit5, 0.5] for name in RANKERS}
    file_hit["vote_prior"] = [0.22, 0.527, 0.627]
    tokens = {name: {"found5": 0.47, "list_tokens": 26.4, "list_per_1k": 17.88} for name in RANKERS}
    for name in sessions:
        tokens[name] |= {"card_tokens": 103.6, "card_per_1k": 4.57, "full_tokens": 8790.8, "full_per_1k": 0.05}
    return {
        "queries": queries,
        "repos": ["iterative/dvc", "pydantic/pydantic"],
        "repo_queries": [124, 95],
        "file_hit": file_hit,
        "file_recall": {name: [0.1, 0.331, 0.4] for name in RANKERS},
        "file_hit_main_ci": {name: {"query_ci": [0.377, 0.487], "repo_ci": [0.296, 0.645]} for name in RANKERS},
        "session_hit5": {name: 0.583 for name in sessions},
        "files_named5": {name: 16.1 for name in sessions},
        "repo_file_hit_main": {name: [0.274, 0.695] for name in RANKERS},
        "macro_file_hit_main": {name: 0.65 for name in RANKERS},
        "tokens": tokens,
        "repo_main_comparison": {
            "metric": "file_hit@5",
            "a": "vote_prior",
            "b": "issue_fix_vote",
            "diff": [0.073, -0.011],
            "query_ci": [[-0.016, 0.161], [-0.116, 0.084]],
        },
        "comparisons": [
            comparison("vote_prior", "hybrid", 0.097, [0.053, 0.14], [0.027, 0.186]),
            comparison("hybrid", "most_files", -0.05, [-0.08, -0.01], [-0.1, -0.02]),
            comparison("vote_prior", "past_fix_files", 0.127, [0.07, 0.18], [-0.014, 0.19]),
            comparison("hybrid", "past_fix_files", 0.03, [-0.033, 0.087], [-0.151, 0.147]),
        ],
    }


def make_files():
    return {
        "ns": [1, 5, 10],
        "main_n": 5,
        "rankers": RANKERS,
        "repos": REPOS,
        "test": make_half(300, 0.43),
        "dev": make_half(297, 0.542),
        "all": make_half(597, 0.486),
        "dev_choice": "hybrid",
    }


class FileBlockTest(unittest.TestCase):
    def test_verdict_needs_both_intervals_to_exclude_zero(self):
        better, worse, queries_only, unclear = make_files()["test"]["comparisons"]
        self.assertEqual(demo._verdict(better), "`vote_prior` is better")
        self.assertEqual(demo._verdict(worse), "`hybrid` is worse")
        self.assertEqual(demo._verdict(queries_only), "clear over queries, not over repositories")
        self.assertEqual(demo._verdict(unclear), "no clear difference")

    def test_headline_and_test_half_tables(self):
        block = demo.render_file_block(make_files())
        self.assertIn("(300 queries)", block)
        self.assertIn("Test half: `vote_prior` 52.7%, `issue_fix_vote` 43.0%", block)
        self.assertIn(
            "`hybrid` vs `past_fix_files` | +3.0 | -3.3 to +8.7 | -15.1 to +14.7 | no clear difference |",
            block,
        )
        self.assertIn("| hybrid | 10.0% | 43.0% | 50.0% | 37.7%-48.7% | 29.6%-64.5% | 33.1% | 58.3% | 16.1 |", block)
        self.assertIn("| vote_prior | 22.0% | 52.7% | 62.7% |", block)
        self.assertIn(
            "| iterative/dvc | 27.4% | 27.4% | 27.4% | 27.4% | 27.4% | 27.4% | +7.3 (-1.6 to +16.1) | 124 |", block
        )
        self.assertIn("| Mean of repositories | 65.0% | 65.0% | 65.0% | 65.0% | 65.0% | 65.0% |  |  |", block)

    def test_token_rows_and_halves(self):
        block = demo.render_file_block(make_files())
        self.assertIn("| hybrid | 0.47 | 26 | 17.88 | 104 | 4.57 | 8791 | 0.05 |", block)
        self.assertIn("| past_fix_files | 0.47 | 26 | 17.88 | n/a | n/a | n/a | n/a |", block)
        self.assertIn("| hybrid | 54.2% | 43.0% | 48.6% |", block)
        self.assertIn("picks `hybrid`, with session hit@5 58.3% there and 58.3% on the test half.", block)

    def test_full_block_puts_the_file_level_first_and_keeps_the_card_note(self):
        block = demo.render_block(make_metrics(), make_files())
        self.assertLess(block.index("### File level"), block.index("### Session level"))
        self.assertIn(demo.CARD_NOTE, block)


class CustomCorpusTest(unittest.TestCase):
    def test_renders_results_of_a_corpus_other_than_the_pinned_data(self):
        tasks = [
            Task(f"t-{i}", "org/repo", f"2024-01-{i + 1:02d} 00:00:00+00:00", "MIT", "issue", ("a.py",))
            for i in range(12)
        ]
        sessions = [
            Session(task.instance_id, i, "m", True, "submitted", (), ("a.py",), ()) for i, task in enumerate(tasks)
        ]
        block = demo.render_block(metrics(tasks, sessions), file_metrics(tasks, sessions))
        self.assertIn("| org/repo | 2 | 2 |", block)
        self.assertNotIn("iterative/dvc", block)


class BlockTest(unittest.TestCase):
    def test_paragraph_and_table_formats(self):
        block = demo.render_session_block(make_metrics(), REPOS)

        self.assertIn(
            "597 queries from 2 repositories (677 tasks, 812 sessions, 138 resolved sessions)",
            block,
        )
        self.assertIn("mean pool has 15.2 sessions", block)
        self.assertIn("3700 tokens", block)
        self.assertIn("37 tokens", block)
        self.assertIn("| `recent` | 10.0% | 37.1% (34.0%-40.0%) |", block)
        self.assertIn("| **`hybrid`** (best) | 50.0% | 64.2% (60.5%-68.0%) |", block)
        self.assertIn("+15.2 (+11.7 to +19.1)", block)
        self.assertIn("| 0.123 |", block)
        self.assertIn("| 5774 | 57 |", block)

    def test_separator_rows_and_cell_spacing(self):
        block = demo.render_session_block(make_metrics(), REPOS)

        self.assertIn("|---|---|---|---|---|---|---|---|", block)
        self.assertIn("|---|---|---|---|---|", block)
        self.assertIn("|---|---|---|---|---|---|", block)
        for line in block.splitlines():
            if line.startswith("|") and not line.startswith("|---"):
                self.assertTrue(line.startswith("| "), line)

    def test_repository_row(self):
        block = demo.render_session_block(make_metrics(), REPOS)
        self.assertIn("| iterative/dvc | 248 | 195 | 16.9% | 26.2% | 47.6% |", block)


class ReplaceTest(unittest.TestCase):
    def test_only_the_block_changes_and_markers_are_kept(self):
        text = "Intro\n<!-- results:start -->\nold\n<!-- results:end -->\nOutro\n"
        expected = "Intro\n<!-- results:start -->\nnew\nblock\n<!-- results:end -->\nOutro\n"
        self.assertEqual(demo.replace_results(text, "new\nblock"), expected)

    def test_missing_marker_is_an_error(self):
        with self.assertRaises(ValueError):
            demo.replace_results("no markers here\n", "new")


class SummaryTest(unittest.TestCase):
    def test_one_line_names_all_three_retrievers(self):
        line = demo.summary(make_metrics(), make_files())
        self.assertNotIn("\n", line)
        self.assertIn("recent 37.1%", line)
        self.assertIn("bm25_issue 48.8%", line)
        self.assertIn("hybrid 64.2% (best)", line)
        self.assertIn("cards 57", line)
        self.assertIn("full 8995", line)
        self.assertIn("test-half file hit@5: vote_prior 52.7%, issue_fix_vote 43.0%", line)


class MainTest(unittest.TestCase):
    def _write_results(self, root):
        results = root / "results"
        results.mkdir()
        (results / "metrics.json").write_text(json.dumps(make_metrics()), encoding="utf-8")
        (results / demo.FILES_NAME).write_text(json.dumps(make_files()), encoding="utf-8")
        return results

    def test_main_prints_the_summary_without_readme(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = self._write_results(Path(tmp))
            with redirect_stdout(StringIO()) as stdout:
                demo.main(["--results", str(results)])
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("hybrid 64.2% (best)", lines[0])

    def test_main_renders_only_between_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = self._write_results(root)
            readme = root / "README.md"
            readme.write_text(
                "Before\n<!-- results:start -->\nOLD BLOCK\n<!-- results:end -->\nAfter\n",
                encoding="utf-8",
            )
            out = root / "nested" / "README.md"
            demo.main(
                [
                    "--results",
                    str(results),
                    "--readme",
                    str(readme),
                    "--out",
                    str(out),
                ]
            )
            text = out.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("Before\n<!-- results:start -->\n"))
        self.assertIn("| `recent`", text)
        self.assertTrue(text.endswith("<!-- results:end -->\nAfter\n"))
        self.assertNotIn("OLD BLOCK", text)

    def test_readme_without_out_is_an_argparse_error(self):
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as caught:
                demo.main(["--readme", "README.md"])
        self.assertNotEqual(caught.exception.code, 0)
