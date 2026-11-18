import math
import unittest

from recallbench.evaluate import BUDGETS, KS, PACK_DEPTH, file_hit, first_files, gain, hit_at, ndcg_at, pack, recall_at


class ConstantsTest(unittest.TestCase):
    def test_spec_constants(self):
        self.assertEqual(KS, (1, 5))
        self.assertEqual(BUDGETS, (500, 1000, 2000, 4000, 8000, 16000))
        self.assertEqual(PACK_DEPTH, 20)


class GainTest(unittest.TestCase):
    def test_share_of_gold_files_named_by_distinct_pointers(self):
        self.assertAlmostEqual(gain(("a.py", "a.py", "b.py"), ("a.py", "b.py", "c.py")), 2 / 3)

    def test_no_gold_files(self):
        self.assertEqual(gain(("a.py",), ()), 0.0)

    def test_no_shared_file(self):
        self.assertEqual(gain(("a.py",), ("b.py",)), 0.0)


class HitAtTest(unittest.TestCase):
    def test_any_gold_file_in_the_first_k_memories(self):
        ranked = [("a.py",), ("b.py", "c.py")]
        gold = ("c.py",)
        self.assertEqual(hit_at(ranked, gold, 1), 0)
        self.assertEqual(hit_at(ranked, gold, 2), 1)

    def test_empty_ranking_empty_gold_and_zero_k(self):
        self.assertEqual(hit_at([], ("a.py",), 5), 0)
        self.assertEqual(hit_at([("a.py",)], (), 5), 0)
        self.assertEqual(hit_at([("a.py",)], ("a.py",), 0), 0)


class RecallAtTest(unittest.TestCase):
    def test_union_of_the_first_k_memories(self):
        ranked = [("a.py",), ("b.py", "c.py")]
        gold = ("a.py", "b.py", "c.py", "d.py")
        self.assertEqual(recall_at(ranked, gold, 1), 1 / 4)
        self.assertEqual(recall_at(ranked, gold, 2), 3 / 4)
        self.assertEqual(recall_at(ranked, gold, 5), 3 / 4)

    def test_empty_ranking_empty_gold_and_zero_k(self):
        self.assertEqual(recall_at([], ("a.py",), 5), 0.0)
        self.assertEqual(recall_at([("a.py",)], (), 5), 0.0)
        self.assertEqual(recall_at([("a.py",)], ("a.py",), 0), 0.0)


class NdcgAtTest(unittest.TestCase):
    def test_ideal_uses_the_whole_pool_not_only_the_ranked_prefix(self):
        ranked = [("b.py",)]
        pool = [("b.py",), ("a.py", "b.py")]
        gold = ("a.py", "b.py")
        self.assertEqual(ndcg_at(ranked, pool, gold, 1), 0.5)

    def test_known_two_position_value(self):
        ranked = [("b.py",), ("a.py",)]
        pool = [("b.py",), ("a.py",)]
        gold = ("a.py",)
        self.assertAlmostEqual(ndcg_at(ranked, pool, gold, 2), 1 / math.log2(3))

    def test_zero_when_the_ideal_is_zero(self):
        self.assertEqual(ndcg_at([("a.py",)], [("a.py",)], ("b.py",), 1), 0.0)
        self.assertEqual(ndcg_at([("a.py",)], [("a.py",)], (), 1), 0.0)


class PackTest(unittest.TestCase):
    def test_keeps_each_memory_that_fits_and_skips_the_rest(self):
        self.assertEqual(pack([100, 300, 200, 400, 500], 600), [0, 1, 2])

    def test_skips_an_oversized_first_memory_and_keeps_walking(self):
        self.assertEqual(pack([500, 100], 200), [1])

    def test_exact_fit(self):
        self.assertEqual(pack([100, 200], 300), [0, 1])

    def test_zero_budget_keeps_nothing_for_positive_costs(self):
        self.assertEqual(pack([10, 20], 0), [])

    def test_depth_limits_the_walk(self):
        self.assertEqual(pack([1, 2, 3, 4], 100, depth=2), [0, 1])

    def test_default_depth_is_twenty(self):
        self.assertEqual(pack([1] * 25, 100), list(range(PACK_DEPTH)))


class FirstFilesTest(unittest.TestCase):
    def test_walks_memories_in_rank_order_and_keeps_each_file_once(self):
        ranked = [("a.py", "b.py"), ("b.py", "c.py"), (), ("d.py",)]
        self.assertEqual(first_files(ranked, 3), ["a.py", "b.py", "c.py"])
        self.assertEqual(first_files(ranked, 10), ["a.py", "b.py", "c.py", "d.py"])
        self.assertEqual(first_files([], 5), [])


class FileHitTest(unittest.TestCase):
    def test_any_shared_file(self):
        self.assertEqual(file_hit(["a.py", "b.py"], ("b.py",)), 1)
        self.assertEqual(file_hit(["a.py"], ("b.py",)), 0)
        self.assertEqual(file_hit([], ()), 0)
