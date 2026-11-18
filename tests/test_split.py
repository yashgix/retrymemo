import unittest

from recallbench.data import Task
from recallbench.split import date_split


def task(repo, day):
    return Task(f"{repo}-{day}", repo, f"2024-01-{day:02d} 00:00:00+00:00", "MIT", "", ())


class DateSplitTest(unittest.TestCase):
    def test_earlier_half_of_each_repo_is_dev_and_test_gets_the_odd_one(self):
        tasks = [task("o/a", 5), task("o/a", 1), task("o/b", 2), task("o/a", 3), task("o/b", 9), task("o/b", 4)]
        dev, test = date_split(tasks, [0, 1, 2, 3, 4, 5])
        self.assertEqual(dev, [1, 2])
        self.assertEqual(test, [0, 3, 4, 5])

    def test_halves_cover_the_queries_once(self):
        tasks = [task("o/a", d) for d in range(1, 8)]
        dev, test = date_split(tasks, [6, 2, 4, 0])
        self.assertEqual((dev, test), ([0, 2], [4, 6]))
