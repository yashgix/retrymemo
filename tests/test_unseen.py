import importlib.util
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from recallbench.data import Session, Task
from recallbench.retrieve import MIN_HISTORY, Corpus
from recallbench.unseen import RANKERS, History

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("unseen_under_test", ROOT / "tools" / "unseen.py")
unseen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unseen)


def task(i, issue, gold, repo="r"):
    return Task(f"t{i}", repo, f"2020-01-{i + 1:02d}", "MIT", issue, gold)


def session(i, edited, turns=("look",)):
    return Session(f"t{i}", i, "m", True, "submitted", (), edited, turns)


TASKS = [
    task(0, "parser crash on tokens", ("src/parser.py",)),
    task(1, "widget colour wrong", ("src/widget.py",)),
    task(2, "slow render", ("src/render.py",)),
    task(3, "parser fails on unicode", ("src/lexer.py",)),
]
SESSIONS = [session(0, ("src/parser.py",)), session(1, ("src/widget.py",)), session(1, ("src/other.py",))]


class HistoryTest(unittest.TestCase):
    def test_fix_hybrid_matches_issue_and_fix_paths(self):
        history = History(Corpus(TASKS, SESSIONS))
        self.assertEqual(history.fix_hybrid_tasks(3)[0], 0)

    def test_fix_hybrid_uses_fix_paths_without_issue_words(self):
        tasks = [*TASKS[:3], task(3, "render module", ("src/x.py",))]
        self.assertEqual(History(Corpus(tasks, SESSIONS)).fix_hybrid_tasks(3)[0], 2)

    def test_session_index_lists_each_task_once(self):
        order = History(Corpus(TASKS, SESSIONS)).session_index_tasks(3)
        self.assertEqual(sorted(order), [0, 1])

    def test_session_index_votes_with_fixes_not_session_files(self):
        files = History(Corpus(TASKS, SESSIONS)).file_lists(3)["session_index_fix"]
        self.assertNotIn("src/other.py", files)

    def test_only_earlier_tasks_reach_a_query(self):
        tasks = [*TASKS, task(4, "parser crash", ("src/later.py",))]
        sessions = [*SESSIONS, session(4, ("src/later.py",))]
        before = History(Corpus(TASKS, SESSIONS)).file_lists(3)
        after = History(Corpus(tasks, sessions)).file_lists(3)
        self.assertEqual(before, after)
        changed = replace(TASKS[3], gold_files=("src/leak.py",))
        own = History(Corpus([*TASKS[:3], changed], SESSIONS)).file_lists(3)
        self.assertEqual(own, before)
        self.assertEqual(set(before), set(RANKERS))


class UnseenQueriesTest(unittest.TestCase):
    def test_needs_new_task_and_enough_session_history(self):
        tasks = [task(i, "x", ("a.py",)) for i in range(MIN_HISTORY + 2)]
        covered = {t.instance_id for t in tasks[:MIN_HISTORY]}
        new_ids = {tasks[2].instance_id, tasks[MIN_HISTORY].instance_id, tasks[MIN_HISTORY + 1].instance_id}
        self.assertEqual(unseen.unseen_queries(tasks, new_ids, covered), [MIN_HISTORY, MIN_HISTORY + 1])


class SelectTest(unittest.TestCase):
    def test_bounds_keep_repos_with_10_to_19_covered_tasks(self):
        repo_of = {f"{r}{i}": r for r, n in (("small", 9), ("mid", 10), ("top", 19), ("big", 20)) for i in range(n)}
        index = [(i, resolved) for i in repo_of for resolved in (True, False, True)]
        repos, picked = unseen.select(index, repo_of, MIN_HISTORY, unseen.MIN_COVERED)
        self.assertEqual(repos, ["mid", "top"])
        self.assertEqual(len(picked), 2 * 29)
        self.assertEqual(unseen.select(index, repo_of)[0], ["big"])


class BuildTest(unittest.TestCase):
    def test_sets_hold_only_unseen_tasks_of_their_repos(self):
        known = [task(i, "x", ("a.py",)) for i in range(MIN_HISTORY)]
        unseen_a = task(MIN_HISTORY, "x", ("a.py",))
        other = [replace(task(i, "y", ("b.py",), repo="o"), instance_id=f"o{i}") for i in range(MIN_HISTORY + 1)]
        known_sessions = [session(i, ("a.py",)) for i in range(MIN_HISTORY)]
        other_sessions = [replace(session(i, ("b.py",)), instance_id=f"o{i}") for i in range(MIN_HISTORY)]
        with (
            mock.patch.object(unseen, "load", return_value=(known, known_sessions)),
            mock.patch.object(unseen, "extra_tasks", return_value=[*known, unseen_a, *other]),
            mock.patch.object(unseen, "other_repo_sessions", return_value=({"o"}, other_sessions)) as loader,
        ):
            corpus, sets = unseen.build(Path("unused"), expand=True)
        self.assertEqual(loader.call_args.args[2], {"r"})
        ids = {name: [corpus.tasks[q].instance_id for q in qs] for name, qs in sets.items()}
        self.assertEqual(ids["A"], [unseen_a.instance_id])
        self.assertEqual(ids["B"], [f"o{MIN_HISTORY}"])
        self.assertEqual(sorted(ids["all"]), sorted(ids["A"] + ids["B"]))


if __name__ == "__main__":
    unittest.main()
