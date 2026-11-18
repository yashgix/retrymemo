import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from recallbench.handoff import created, handoff, opened

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("handoff_under_test", ROOT / "tools" / "handoff.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def attempt(task, row, resolved, first_open=None, edited=(), commands=(), split="dev"):
    return {
        "instance_id": task,
        "row": row,
        "resolved": resolved,
        "first_open": first_open or {},
        "edited": list(edited),
        "commands": [list(c) for c in commands],
        "split": split,
        "steps": 10,
    }


class HandoffTest(unittest.TestCase):
    def test_scratch_files_the_attempt_created_are_dropped(self):
        a = attempt("t", 0, False, {"reproduce.py": 1, "src/a.py": 2}, commands=[("create reproduce.py", "")])
        self.assertEqual(created(a), {"reproduce.py"})
        self.assertEqual(opened(a), ["src/a.py"])

    def test_opened_keeps_first_open_order(self):
        a = attempt("t", 0, False, {"b.py": 3, "a.py": 1})
        self.assertEqual(opened(a), ["a.py", "b.py"])

    def test_edited_files_outrank_opened_ones_then_baseline_follows(self):
        a = attempt("t", 0, False, {"seen.py": 1, "fixed.py": 2}, edited=["fixed.py"])
        self.assertEqual(handoff([a], ["base.py", "seen.py"], 2.0), ["fixed.py", "seen.py", "base.py"])

    def test_ties_follow_the_baseline(self):
        a = attempt("t", 0, False, {"z.py": 1, "y.py": 2})
        self.assertEqual(handoff([a], ["y.py"], 2.0)[:2], ["y.py", "z.py"])


class FirstRetriesTest(unittest.TestCase):
    def test_pairs_only_tasks_of_the_split_whose_lowest_row_failed(self):
        records = [
            attempt("a", 5, False),
            attempt("a", 2, False),
            attempt("b", 1, True),
            attempt("b", 3, False),
            attempt("c", 4, False),
            attempt("d", 6, False, split="test"),
            attempt("d", 7, False, split="test"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a.jsonl.gz"
            with gzip.open(path, "wt") as f:
                f.writelines(json.dumps(r) + "\n" for r in records)
            pairs = tool.first_retries(path, "dev")
        self.assertEqual([(a["instance_id"], a["row"], b["row"]) for a, b in pairs], [("a", 2, 5)])


class SummaryTest(unittest.TestCase):
    def test_rates_disagreements_and_missed_retries(self):
        pairs = [(attempt(t, 0, False), attempt(t, 1, r)) for t, r in (("a", False), ("b", True), ("c", False))]
        hit1 = {
            "handoff": np.array([1.0, 0.0, 1.0]),
            "retry_agent": np.array([0.0, 1.0, 1.0]),
            "fix_hybrid": np.zeros(3),
        }
        hit5 = {name: np.ones(3) for name in hit1}
        out = tool.summary(["r1", "r1", "r2"], hit1, hit5, [None, 3, 5], pairs)
        self.assertEqual(out["hit1_handoff_right_retry_wrong"], 1)
        self.assertEqual(out["hit1_handoff_wrong_retry_right"], 1)
        self.assertEqual((out["retry_resolved"], out["retry_never_opened_needed_file"]), (1, 1))
        self.assertAlmostEqual(out["handoff"]["hit1"], 2 / 3, places=6)
        self.assertEqual(out["handoff"]["hit1_when_retry_never_opened"], 1.0)
        self.assertEqual(out["hit1:handoff-retry_agent"]["diff"], 0.0)


if __name__ == "__main__":
    unittest.main()
