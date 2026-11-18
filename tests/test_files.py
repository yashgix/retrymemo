import unittest

from recallbench.data import Session, Task
from recallbench.files import COMPARISONS, HALVES, MAIN_N, NS, RANKERS, SESSION_RANKERS, file_metrics
from recallbench.retrieve import MIN_HISTORY, RETRIEVERS

NOISE = tuple(f"noise{i}.py" for i in range(8))


def dataset():
    """Ten past tasks fix x.py six times, then y.py four times. Two queries follow: a dev one fixing y.py, then a
    test one fixing x.py.

    The first session names x.py, eight unrelated files and y.py, so it leads the most_files ranking.
    """
    tasks = [
        Task(
            f"a-{i}", "o/a", f"2024-01-{i + 1:02d} 00:00:00+00:00", "MIT", f"issue {i}", ("x.py" if i < 6 else "y.py",)
        )
        for i in range(MIN_HISTORY)
    ]
    tasks.append(Task("a-q1", "o/a", "2024-02-01 00:00:00+00:00", "MIT", "query", ("y.py",)))
    tasks.append(Task("a-q2", "o/a", "2024-02-02 00:00:00+00:00", "MIT", "query", ("x.py",)))
    sessions = [Session("a-0", 0, "m", False, "submitted", (*NOISE, "y.py"), ("x.py",), ())]
    sessions += [
        Session(t.instance_id, i, "m", True, "submitted", (), t.gold_files, ()) for i, t in enumerate(tasks[1:-2], 1)
    ]
    return tasks, sessions


class FileMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = file_metrics(*dataset())

    def test_rankers_halves_and_constants(self):
        self.assertEqual((NS, MAIN_N, HALVES), ((1, 5, 10), 5, ("test", "dev", "all")))
        self.assertEqual(SESSION_RANKERS, (*RETRIEVERS, "most_files"))
        self.assertEqual(RANKERS, (*SESSION_RANKERS, "past_fix_files", "issue_fix_vote", "vote_prior"))
        self.assertEqual(self.m["repos"], ["o/a"])
        self.assertEqual([self.m[h]["queries"] for h in HALVES], [1, 1, 2])
        self.assertEqual(set(self.m["test"]["file_hit"]), set(RANKERS))
        self.assertIn(self.m["dev_choice"], RETRIEVERS)

    def test_past_fix_files_ranks_the_most_often_fixed_file_first(self):
        self.assertEqual(self.m["dev"]["file_hit"]["past_fix_files"], [0.0, 1.0, 1.0])
        self.assertEqual(self.m["test"]["file_hit"]["past_fix_files"], [1.0, 1.0, 1.0])
        self.assertEqual(self.m["all"]["file_hit"]["past_fix_files"], [0.5, 1.0, 1.0])

    def test_most_files_session_hits_but_its_first_five_files_miss(self):
        dev = self.m["dev"]
        self.assertEqual(dev["session_hit5"]["most_files"], 1.0)
        self.assertEqual(dev["file_hit"]["most_files"], [0.0, 0.0, 1.0])
        self.assertEqual(dev["files_named5"]["most_files"], 10.0)

    def test_comparisons_follow_the_list_and_collapse_on_one_query(self):
        test = self.m["test"]
        self.assertEqual([(c["metric"], c["a"], c["b"]) for c in test["comparisons"]], list(COMPARISONS))
        main = NS.index(MAIN_N)
        for c in test["comparisons"]:
            if c["metric"] == "file_hit@5":
                want = test["file_hit"][c["a"]][main] - test["file_hit"][c["b"]][main]
            else:
                want = test["session_hit5"][c["a"]] - test["session_hit5"][c["b"]]
            self.assertAlmostEqual(c["diff"], want, places=6)
            self.assertEqual(c["query_ci"], [c["diff"]] * 2)
            self.assertEqual(c["repo_ci"], [c["diff"]] * 2)

    def test_token_costs(self):
        prior = self.m["dev"]["tokens"]["past_fix_files"]
        self.assertEqual(prior, {"found5": 1.0, "list_tokens": 3.0, "list_per_1k": 333.333333})
        self.assertEqual(
            set(self.m["dev"]["tokens"]["hybrid"]) - set(prior),
            {"card_tokens", "card_per_1k", "full_tokens", "full_per_1k"},
        )

    def test_per_repo_and_macro_values(self):
        main = NS.index(MAIN_N)
        for name in RANKERS:
            self.assertEqual(self.m["all"]["repo_file_hit_main"][name], [self.m["all"]["file_hit"][name][main]])
            self.assertEqual(self.m["all"]["macro_file_hit_main"][name], self.m["all"]["file_hit"][name][main])

    def test_no_queries_gives_only_the_counts(self):
        tasks, sessions = dataset()
        m = file_metrics(tasks[:3], sessions[:3])
        self.assertEqual([(m[h]["queries"], m[h]["repos"]) for h in HALVES], [(0, [])] * 3)
        self.assertNotIn("file_hit", m["test"])
        self.assertIsNone(m["dev_choice"])

    def test_issue_fix_vote_uses_earlier_fixes_only(self):
        # The dev query's earlier fixes name only x.py and y.py, so both are in its first five files.
        self.assertEqual(self.m["dev"]["file_hit"]["issue_fix_vote"], [0.0, 1.0, 1.0])

    def test_per_repo_main_comparison(self):
        paired = self.m["all"]["repo_main_comparison"]
        main = NS.index(MAIN_N)
        a, b = paired["a"], paired["b"]
        self.assertEqual((paired["metric"], a, b), COMPARISONS[0])
        self.assertEqual(paired["diff"], [self.m["all"]["file_hit"][a][main] - self.m["all"]["file_hit"][b][main]])
        self.assertEqual(len(paired["query_ci"]), 1)
