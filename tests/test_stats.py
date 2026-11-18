import unittest

import numpy as np

from recallbench.stats import BOOTSTRAP_DRAWS, SEED, draws, interval, query_interval, r6, repo_interval


class ConstantsTest(unittest.TestCase):
    def test_v1_constants(self):
        self.assertEqual((SEED, BOOTSTRAP_DRAWS), (20261114, 1000))


class DrawsTest(unittest.TestCase):
    def test_shape_and_same_draws_every_call(self):
        first = draws(7)
        self.assertEqual(first.shape, (BOOTSTRAP_DRAWS, 7))
        self.assertTrue((first == draws(7)).all())
        self.assertTrue(((first >= 0) & (first < 7)).all())


class IntervalTest(unittest.TestCase):
    def test_takes_the_2_5th_and_97_5th_percentile_of_sorted_means(self):
        means = np.random.default_rng(0).permutation(BOOTSTRAP_DRAWS)
        self.assertEqual(interval(means), (25.0, 974.0))

    def test_query_interval_of_a_constant_is_that_constant(self):
        self.assertEqual(query_interval([0.5] * 4, draws(4)), (0.5, 0.5))

    def test_r6(self):
        self.assertEqual(r6(np.float64(1 / 3)), 0.333333)


class RepoIntervalTest(unittest.TestCase):
    def test_pools_the_queries_of_the_drawn_repos(self):
        values = [1.0, 1.0, 1.0, 0.0]
        repo_of = np.array([0, 0, 0, 1])
        both = np.tile([0, 1], (BOOTSTRAP_DRAWS, 1))
        self.assertEqual(repo_interval(values, repo_of, both), (0.75, 0.75))
        first_only = np.zeros((BOOTSTRAP_DRAWS, 2), dtype=int)
        self.assertEqual(repo_interval(values, repo_of, first_only), (1.0, 1.0))
