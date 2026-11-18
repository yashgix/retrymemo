import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from example_terms import RULES

from replay import ARMS, SEEDS, analyze, freeze, redact
from replay.memory import payload_problems
from replay.scoring import cells_done
from replay.tasks import NAMES, load

MANIFEST = {t: {"gold_ranks": {"g": 3}, "texts": {"retrieved-1": {"gold_items": []}}} for t in NAMES}


def run(task, arm, seed, avoided=False, passed=None, **extra):
    row = {
        "task": task,
        "arm": arm,
        "seed": seed,
        "exit_status": "Submitted",
        "steps": 10,
        "seconds": 60,
        "cost": 0.02,
        "tokens": {"input": 1000, "cached_input": 900, "output": 100},
        "scoring_error": None,
        "pass": avoided if passed is None else passed,
        "avoided": avoided,
    }
    return row | extra


def grid(avoided=lambda task, arm, seed: arm == "oracle"):
    return [run(t, a, s, avoided(t, a, s)) for t in NAMES for a in ARMS for s in range(1, SEEDS + 1)]


class Statistics(unittest.TestCase):
    def test_wilson_intervals(self):
        self.assertEqual(analyze.wilson(5, 5), [0.5655, 1.0])
        self.assertEqual(analyze.wilson(0, 5), [0.0, 0.4345])
        self.assertIsNone(analyze.wilson(0, 0))

    def test_sign_flip_is_exact_over_all_patterns(self):
        self.assertEqual(analyze.sign_flip_p([1, 1, 1, 1]), 0.125)
        self.assertEqual(analyze.sign_flip_p([0, 0, 0, 0]), 1.0)
        self.assertEqual(analyze.sign_flip_p([0.4, -0.4, 0.4, -0.4]), 1.0)

    def test_holm_is_monotone_and_capped(self):
        self.assertEqual(analyze.holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])
        self.assertEqual(analyze.holm([0.9, 0.8]), [1.0, 1.0])

    def test_bootstrap_is_reproducible(self):
        per_task = {t: ([True, False, True], [False, False, True]) for t in analyze.PRIMARY_TASKS}
        self.assertEqual(analyze.bootstrap_ci(per_task), analyze.bootstrap_ci(per_task))


class WhichRunsCount(unittest.TestCase):
    def test_crashes_and_scoring_errors_are_missing_not_failures(self):
        runs = grid()
        runs[0] |= {"steps": 0, "exit_status": "crash:RuntimeError: docker"}
        runs[1] |= {"steps": 1, "cost": 0.0, "exit_status": "crash:APIConnectionError: outage"}  # first call failed
        runs[2] |= {"steps": 9, "exit_status": "crash:RuntimeError: Global cost/call limit exceeded"}  # cap cut
        runs[3] |= {"scoring_error": "TimeoutExpired"}
        runs[4] |= {"exit_status": "LimitsExceeded"}  # the agent's own ending counts
        runs[5] |= {"exit_status": "ContextLimit: BadRequestError: maximum context length"}  # so does this
        kept, failures = analyze.counted(runs)
        self.assertEqual(len(failures), 4)
        self.assertIn(tuple(runs[4][k] for k in ("task", "arm", "seed")), kept)
        self.assertIn(tuple(runs[5][k] for k in ("task", "arm", "seed")), kept)

    def test_two_counted_runs_for_one_cell_are_refused(self):
        runs = grid()
        with self.assertRaises(ValueError):
            analyze.counted([*runs, runs[0]])

    def test_a_missing_cell_is_retried_once(self):
        crash = run("lock", "none", 1, exit_status="crash:Timeout")
        self.assertNotIn(("lock", "none", 1), cells_done([crash]))
        self.assertIn(("lock", "none", 1), cells_done([crash, crash]))
        self.assertIn(("lock", "oracle", 1), cells_done([run("lock", "oracle", 1)]))

    def test_a_seed_missing_one_cell_is_not_complete(self):
        runs = [r for r in grid() if not (r["task"] == "onnx" and r["arm"] == "random" and r["seed"] == 4)]
        kept, _ = analyze.counted(runs)
        self.assertEqual(analyze.complete_seeds(kept), [1, 2, 3, 5])

    def test_too_few_complete_seeds_gives_no_reading(self):
        result = analyze.analyze([r for r in grid() if r["seed"] <= 2], MANIFEST)
        self.assertFalse(result["enough_data"])
        self.assertEqual({r["reading"] for r in result["primary"].values()}, {"too little data"})


class Analysis(unittest.TestCase):
    def test_a_clear_oracle_effect_reads_as_consistent_benefit(self):
        result = analyze.analyze(grid(), MANIFEST)
        oracle = result["primary"]["oracle vs none"]
        self.assertEqual((oracle["mean_difference"], oracle["p"], oracle["bootstrap_95"]), (1.0, 0.125, [1.0, 1.0]))
        self.assertEqual(oracle["reading"], "consistent benefit")
        self.assertEqual(result["primary"]["retrieved vs none"]["reading"], "no clear effect")
        self.assertEqual(oracle["p_holm"], 0.375)  # two comparisons share the smallest p, 3 × 0.125

    def test_a_clear_oracle_harm_reads_as_consistent_harm(self):
        result = analyze.analyze(grid(lambda t, a, s: a != "oracle"), MANIFEST)
        self.assertEqual(result["primary"]["oracle vs none"]["reading"], "consistent harm")

    def test_bootstrap_interval_brackets_a_mixed_effect(self):
        per_task = {
            "a": ([True] * 4 + [False], [False] * 5),
            "b": ([True, False] * 2 + [True], [True, False] * 2 + [False]),
        }
        low, high = analyze.bootstrap_ci(per_task)
        self.assertTrue(-1 <= low < 0.5 < high <= 1)

    def test_simlink_is_never_pooled(self):
        result = analyze.analyze(grid(lambda t, a, s: t == "simlink" and a == "oracle"), MANIFEST)
        self.assertNotIn("simlink", result["primary"]["oracle vs none"]["tasks"])
        self.assertEqual(result["primary"]["oracle vs none"]["mean_difference"], 0.0)
        self.assertEqual(result["simlink"]["oracle vs none"]["mean_difference"], 1.0)

    def test_lesson_groups_split_the_primary_tasks(self):
        result = analyze.analyze(grid(lambda t, a, s: a == "oracle" and t == "fczero"), MANIFEST)
        self.assertEqual(sorted(result["lesson_in_repo"]["oracle vs none"]["tasks"]), ["lock", "onnx", "verifier"])
        self.assertEqual(result["lesson_in_repo"]["oracle vs none"]["mean_difference"], 0.0)
        self.assertEqual(result["lesson_not_in_repo"]["oracle vs none"]["mean_difference"], 1.0)

    def test_pass_and_avoided_are_reported_apart(self):
        runs = grid(lambda t, a, s: False)
        for r in runs:
            r["pass"] = r["arm"] == "none"
        result = analyze.analyze(runs, MANIFEST)
        self.assertEqual(result["secondary_pass"]["oracle vs none"]["mean_difference"], -1.0)
        self.assertEqual(result["primary"]["oracle vs none"]["mean_difference"], 0.0)

    def test_process_and_cost(self):
        result = analyze.analyze(grid(), MANIFEST)
        self.assertEqual(result["process"]["overall"]["oracle"]["cost"]["mean"], 0.02)
        self.assertEqual(result["process"]["lock"]["none"]["cached_input"]["median"], 900)
        self.assertEqual(result["total_cost"], round(0.02 * len(NAMES) * len(ARMS) * SEEDS, 6))


class Freeze(unittest.TestCase):
    def test_frozen_files_cover_the_analysis_and_not_the_manifest_itself(self):
        found = freeze.files()
        for name in (
            "replay/analyze.py",
            "replay/ANALYSIS.md",
            "replay/memory/manifest.json",
            "results/replay-validation.json",
            "tests/test_replay_analysis.py",
        ):
            self.assertIn(name, found)
        self.assertNotIn("replay/FROZEN.json", found)

    def test_check_reports_a_changed_file_and_image(self):
        frozen = {"files": {"a": "1", "b": "2"}, "images": {"img": "x"}, "packages": {"pkg": "p"}}
        now = {
            "files": {"a": "1", "b": "3", "c": "4"},
            "images": {"img": "y"},
            "packages": {"pkg": "q", "new.pth": "n"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "FROZEN.json"
            path.write_text(json.dumps(frozen))
            with (
                mock.patch.object(freeze, "current", return_value=now),
                mock.patch.object(freeze, "FROZEN", path),
                mock.patch.object(freeze, "tagged", return_value="an older copy"),
            ):
                problems = freeze.check()
        self.assertEqual(
            sorted(problems),
            [
                f"FROZEN.json differs from the copy in tag {freeze.TAG}",
                "changed or added: b",
                "changed or added: c",
                "image changed: img",
                "package added: new.pth",
                "package changed or removed: pkg",
            ],
        )


class Provenance(unittest.TestCase):
    def test_runs_from_another_freeze_or_the_excluded_run_are_refused(self):
        runs = [run("lock", "none", 1, freeze="abc"), run("lock", "none", 1)]  # the second has no freeze id
        self.assertEqual(len(analyze.provenance_problems(runs, "abc")), 1)
        self.assertEqual(analyze.provenance_problems(runs[:1], "abc"), [])

    def test_installed_files_are_hashed_whether_listed_or_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "pkg").mkdir()
            (site / "pkg" / "mod.py").write_text("x = 1")
            with mock.patch.object(freeze, "SITE", site):
                before = freeze.packages()
                (site / "pkg" / "mod.py").write_text("x = 2")
                changed = freeze.packages()
                (site / "_startup.pth").write_text("import os")  # a file no RECORD lists
                added = freeze.packages()
        self.assertNotEqual(before, changed)
        self.assertIn("_startup.pth", added)


@unittest.skipUnless(load("lock")["source_path"].exists(), "needs the private task repositories (REPLAY_SOURCES)")
@mock.patch.object(redact, "RULES", RULES)
class Payload(unittest.TestCase):
    def test_refuses_private_text_and_real_emails_only(self):
        task = load("lock")
        self.assertEqual(payload_problems(task, "lock order notes, a reporter, r@sink.test"), [])
        found = payload_problems(task, "see /Users/x and the Northwind (Batch W26) pitch notes, mail me@example.com")
        self.assertEqual(len(found), 5)


if __name__ == "__main__":
    unittest.main()
