import unittest

import numpy as np

from agent.analyze_v5 import compare, comparisons, disagreements, mcnemar, split_tasks


def run(task: str, arm: str, repeat: int, status: str = "Submitted", **measures) -> dict:
    return {"task": task, "arm": arm, "repeat": repeat, "exit_status": status} | measures


class SplitTasksTest(unittest.TestCase):
    def test_complete_crashed_and_incomplete_tasks_are_told_apart(self):
        runs = [run("t1", a, k) for a in "AB" for k in (1, 2)]
        runs += [run("t2", "A", 1), run("t2", "B", 1, "crash:RuntimeError: x")]
        runs += [run("t3", "A", 1), run("t3", "B", 1)]
        self.assertEqual(split_tasks(runs, ["A", "B"], 2), (["t1"], ["t2"], ["t3"]))


class McNemarTest(unittest.TestCase):
    def test_exact_two_sided_values(self):
        self.assertEqual(mcnemar(0, 0), 1.0)
        self.assertEqual(mcnemar(3, 3), 1.0)
        self.assertAlmostEqual(mcnemar(0, 6), 2 / 64, delta=1e-6)
        self.assertAlmostEqual(mcnemar(1, 7), 2 * 9 / 256, delta=1e-6)


class CompareTest(unittest.TestCase):
    def test_a_steady_saving_is_clear_and_noise_is_not(self):
        base = np.full(40, 20.0)
        repo_of = np.arange(40) // 2
        saving = compare(base, base - 2 + np.tile([0.5, -0.5], 20), repo_of, 20)
        self.assertEqual(saving["diff"], -2.0)
        self.assertAlmostEqual(saving["relative"], -0.1)
        self.assertTrue(saving["clear"])
        self.assertFalse(compare(base, base + np.tile([3.0, -3.0], 20), repo_of, 20)["clear"])

    def test_clear_needs_the_repository_interval_too(self):
        # One big repository carries the whole saving: tasks agree, repositories do not.
        base = np.full(40, 20.0)
        repo_of = np.array([0] * 30 + list(range(1, 11)))
        other = base - np.array([1.0] * 30 + [-2.0, 2.0] * 5)
        result = compare(base, other, repo_of, 11)
        self.assertTrue(result["query_ci"][1] < 0)
        self.assertFalse(result["clear"])


class ComparisonsTest(unittest.TestCase):
    RUNS = [
        run("t1", "A", 1, steps=10, cost=0.1, resolved=True),
        run("t1", "A", 2, steps=12, cost=0.1, resolved=True),
        run("t1", "B", 1, steps=8, cost=0.2, resolved=False),
        run("t1", "B", 2, steps=8, cost=0.2, resolved=True),
        run("t2", "A", 1, steps=20, cost=0.3, resolved=False),
        run("t2", "A", 2, steps=20, cost=0.3, resolved=False),
        run("t2", "B", 1, steps=17, cost=0.3, resolved=True),
        run("t2", "B", 2, steps=19, cost=0.3, resolved=True),
    ]

    def test_runs_pair_by_task_and_repeat(self):
        self.assertEqual(disagreements(self.RUNS, {"t1", "t2"}, "A", "B"), (1, 2))
        self.assertEqual(disagreements(self.RUNS, {"t1"}, "A", "B"), (1, 0))

    def test_measures_are_task_means_over_repeats(self):
        out = comparisons(self.RUNS, ["t1", "t2"], ["A", "B"], {"t1": "r/x", "t2": "r/y"})
        self.assertEqual((out["tasks"], out["repos"]), (2, 2))
        self.assertEqual(out["B-A"]["steps"]["base"], 15.5)
        self.assertEqual(out["B-A"]["steps"]["diff"], -2.5)
        self.assertEqual(out["B-A"]["resolved"]["diff"], 0.25)

    def test_a_subgroup_gets_its_own_repository_index(self):
        out = comparisons(self.RUNS, ["t2"], ["A", "B"], {"t1": "r/x", "t2": "r/y"})
        self.assertEqual((out["tasks"], out["repos"]), (1, 1))
        self.assertEqual(out["B-A"]["steps"]["diff"], -2.0)


if __name__ == "__main__":
    unittest.main()
