import math
import unittest

from recallbench.evaluate import BUDGETS, KS, PACK_DEPTH, gain, hit_at, ndcg_at, pack, recall_at


class Scoring(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(KS, (1, 5))
        self.assertEqual(BUDGETS, (500, 1000, 2000, 4000, 8000, 16000))
        self.assertEqual(PACK_DEPTH, 20)

    def test_gain_hit_recall(self):
        self.assertEqual(gain(("a.py", "b.py", "x.py"), ("a.py", "c.py")), 0.5)
        self.assertEqual(gain((), ("a.py",)), 0.0)
        ranked = [("x.py",), ("a.py",), ()]
        self.assertEqual([hit_at(ranked, ("a.py",), k) for k in (1, 2, 5)], [0, 1, 1])
        self.assertIsInstance(hit_at(ranked, ("a.py",), 2), int)
        self.assertEqual(hit_at([], ("a.py",), 5), 0)
        ranked = [("a.py",), ("b.py", "a.py")]
        gold = ("a.py", "b.py", "c.py")
        self.assertAlmostEqual(recall_at(ranked, gold, 1), 1 / 3)
        self.assertAlmostEqual(recall_at(ranked, gold, 5), 2 / 3)
        self.assertEqual(recall_at([], gold, 5), 0.0)

    def test_ndcg(self):
        ranked = [("x.py",), ("a.py",)]
        pool = [("a.py",), ("x.py",), ("a.py", "b.py")]
        expected = (1 / math.log2(3)) / (1 + 1 / math.log2(3))
        self.assertAlmostEqual(ndcg_at(ranked, pool, ("a.py",), 5), expected)
        self.assertAlmostEqual(ndcg_at(ranked, pool, ("a.py", "b.py"), 1), 0.0)
        best = [("a.py", "b.py"), ("a.py",)]
        self.assertAlmostEqual(ndcg_at(best, pool, ("a.py", "b.py"), 2), 1.0)
        self.assertEqual(ndcg_at([("x.py",)], [("x.py",)], ("a.py",), 5), 0.0)

    def test_pack(self):
        self.assertEqual(pack([5, 100, 3, 2], 10), [0, 2, 3])
        self.assertEqual(pack([11, 4, 6, 1], 10), [1, 2])
        self.assertEqual(pack([1] * 30, 100), list(range(20)))
        self.assertEqual(pack([1] * 30, 100, depth=5), [0, 1, 2, 3, 4])
        self.assertEqual(pack([], 10), [])
