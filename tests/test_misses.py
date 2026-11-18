import importlib.util
import unittest
from pathlib import Path

from recallbench.data import Session, Task
from recallbench.retrieve import Corpus

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("misses_under_test", ROOT / "tools" / "misses.py")
misses = importlib.util.module_from_spec(spec)
spec.loader.exec_module(misses)

MANY = tuple(f"f{i:02d}.py" for i in range(12))


def corpus(gold):
    tasks = [
        Task("t-0", "o/a", "2024-01-01 00:00:00+00:00", "MIT", "issue", ("f00.py",)),
        Task("t-q", "o/a", "2024-01-02 00:00:00+00:00", "MIT", "issue", (gold,)),
    ]
    sessions = [Session("t-0", 0, "m", False, "submitted", ("g.py",), MANY, ())]
    return Corpus(tasks, sessions)


class ClassifyTest(unittest.TestCase):
    def test_a_hit_is_not_a_miss(self):
        self.assertIsNone(misses.classify(corpus("f01.py"), 1))

    def test_a_file_nothing_earlier_names_is_new_to_the_history(self):
        miss = misses.classify(corpus("z.py"), 1)
        self.assertEqual(miss, {"category": misses.CATEGORIES[0], "best_rank": None})

    def test_a_gold_file_buried_by_a_voting_session_with_many_files_is_noise(self):
        miss = misses.classify(corpus("g.py"), 1)
        self.assertEqual(miss, {"category": misses.CATEGORIES[2], "best_rank": 13})
