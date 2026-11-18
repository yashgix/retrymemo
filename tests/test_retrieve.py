import unittest
from collections import Counter
from unittest import mock

from recallbench.data import Session, Task
from recallbench.retrieve import MIN_HISTORY, RETRIEVERS, RRF_K, STALE_DECAY, Corpus
from recallbench.text import session_text, words


def task(
    instance_id,
    repo="org/repo",
    created_at="2024-01-01 00:00:00+00:00",
    issue="",
    gold_files=(),
):
    return Task(instance_id, repo, created_at, "MIT", issue, tuple(gold_files))


def session(instance_id, resolved=True, opened=(), edited=(), turns=()):
    return Session(
        instance_id,
        0,
        "swe-agent-llama-70b",
        resolved,
        "submitted",
        tuple(opened),
        tuple(edited),
        tuple(turns),
    )


class ConstantsTest(unittest.TestCase):
    def test_spec_constants(self):
        self.assertEqual((MIN_HISTORY, RRF_K, STALE_DECAY), (10, 60, 0.8))
        self.assertEqual(
            RETRIEVERS,
            ("recent", "bm25_issue", "bm25_session", "bm25_paths", "hybrid", "resolved_only", "fresh"),
        )


class PoolTest(unittest.TestCase):
    def test_pool_is_same_repo_and_strictly_earlier(self):
        tasks = [
            task("a-1", "org/a", "2024-01-01 00:00:00+00:00"),
            task("a-2", "org/a", "2024-01-05 00:00:00+00:00"),
            task("b-1", "org/b", "2024-01-02 00:00:00+00:00"),
            task("a-3", "org/a", "2024-01-02 00:00:00+00:00"),
            task("a-q", "org/a", "2024-01-05 00:00:00+00:00"),
        ]
        sessions = [session("a-1"), session("a-2"), session("b-1"), session("a-3")]
        corpus = Corpus(tasks, sessions)
        self.assertEqual(corpus.pool(4), [0, 3])


class QueriesTest(unittest.TestCase):
    def test_queries_need_min_history_earlier_tasks(self):
        tasks = [
            task("a-0", "org/a", "2024-01-01 00:00:00+00:00"),
            task("a-1", "org/a", "2024-01-02 00:00:00+00:00"),
            task("a-2", "org/a", "2024-01-03 00:00:00+00:00"),
            task("a-3", "org/a", "2024-01-04 00:00:00+00:00"),
            task("b-0", "org/b", "2024-01-01 00:00:00+00:00"),
            task("b-1", "org/b", "2024-01-02 00:00:00+00:00"),
            task("b-2", "org/b", "2024-01-03 00:00:00+00:00"),
        ]
        corpus = Corpus(tasks, [])
        with mock.patch("recallbench.retrieve.MIN_HISTORY", 2):
            self.assertEqual(corpus.queries(), [2, 3, 6])


class LaterFixesTest(unittest.TestCase):
    def test_counts_only_fixes_strictly_between_that_share_a_file(self):
        tasks = [
            task("a-old", "org/a", "2024-01-02 00:00:00+00:00", gold_files=("x.py",)),
            task("a-q", "org/a", "2024-01-10 00:00:00+00:00", gold_files=("q.py",)),
            task("a-fix", "org/a", "2024-01-03 00:00:00+00:00", gold_files=("x.py",)),
            task("a-other", "org/a", "2024-01-05 00:00:00+00:00", gold_files=("z.py",)),
            task("a-after", "org/a", "2024-01-11 00:00:00+00:00", gold_files=("x.py",)),
            task("b-fix", "org/b", "2024-01-04 00:00:00+00:00", gold_files=("x.py",)),
        ]
        sessions = [session("a-old", opened=("x.py",)), session("a-old")]
        corpus = Corpus(tasks, sessions)
        self.assertEqual(corpus.later_fixes(1, 0), 1)
        self.assertEqual(corpus.later_fixes(1, 1), 0)


class CorpusDocumentsTest(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            task("a-1", "org/a", "2024-01-01 00:00:00+00:00", issue="Fix parse_obj in a.py"),
            task("a-q", "org/a", "2024-01-02 00:00:00+00:00", issue="parse_obj crash"),
        ]
        self.sessions = [
            session("a-1", opened=("pkg/a.py",), edited=("pkg/b.py",), turns=("look at parse_obj", "apply")),
            session("a-1", resolved=False, opened=("pkg/c.py",), turns=("other",)),
        ]
        self.corpus = Corpus(self.tasks, self.sessions)

    def test_keeps_the_input_lists_and_builds_one_document_per_memory(self):
        self.assertIs(self.corpus.tasks, self.tasks)
        self.assertIs(self.corpus.sessions, self.sessions)
        self.assertEqual(len(self.corpus._issue_docs), 2)
        self.assertEqual(len(self.corpus._session_docs), 2)
        self.assertEqual(len(self.corpus._path_docs), 2)
        self.assertEqual(self.corpus._session_task, [0, 0])
        self.assertEqual(self.corpus._issue_docs[0], Counter(words(self.tasks[0].issue)))
        self.assertEqual(
            self.corpus._session_docs[0],
            Counter(words(session_text(self.tasks[0], self.sessions[0]))),
        )
        self.assertEqual(
            self.corpus._path_docs[0],
            Counter(words(" ".join(self.sessions[0].pointers))),
        )

    def test_bm25_issue_has_one_document_per_session(self):
        captured = {}

        def fake_bm25(query, docs):
            captured["query"] = query
            captured["docs"] = docs
            return [0.5, 0.5]

        with mock.patch("recallbench.retrieve.bm25", side_effect=fake_bm25):
            self.assertEqual(self.corpus.rank("bm25_issue", 1), [0, 1])
        self.assertEqual(captured["query"], words(self.tasks[1].issue))
        self.assertEqual(captured["docs"], [self.corpus._issue_docs[0], self.corpus._issue_docs[0]])


class RankingTest(unittest.TestCase):
    def make_corpus(self):
        tasks = [
            task("a-1", "org/a", "2024-01-01 00:00:00+00:00", issue="alpha", gold_files=("c.py",)),
            task("a-2", "org/a", "2024-01-02 00:00:00+00:00", issue="beta", gold_files=("c.py",)),
            task("a-3", "org/a", "2024-01-03 00:00:00+00:00", issue="gamma", gold_files=("alpha.py",)),
            task("a-q", "org/a", "2024-01-04 00:00:00+00:00", issue="alpha", gold_files=("q.py",)),
        ]
        sessions = [
            session("a-1", resolved=False, opened=("c.py",), turns=("alpha",)),
            session("a-2", resolved=True, opened=("alpha.py",), turns=("beta",)),
            session("a-3", resolved=True, opened=("b.py",), turns=("gamma",)),
        ]
        return Corpus(tasks, sessions)

    def test_recent_orders_newer_tasks_first_then_lower_session_index(self):
        tasks = [
            task("a-1", "org/a", "2024-01-01 00:00:00+00:00"),
            task("a-2", "org/a", "2024-01-02 00:00:00+00:00"),
            task("a-q", "org/a", "2024-01-03 00:00:00+00:00"),
        ]
        sessions = [session("a-1"), session("a-2"), session("a-2")]
        corpus = Corpus(tasks, sessions)
        self.assertEqual(corpus.pool(2), [0, 1, 2])
        self.assertEqual(corpus.rank("recent", 2), [1, 2, 0])

    def test_hybrid_fuses_session_and_path_ranks(self):
        corpus = self.make_corpus()
        pool = corpus.pool(3)
        self.assertEqual(pool, [0, 1, 2])
        session_order = corpus.rank("bm25_session", 3)
        path_order = corpus.rank("bm25_paths", 3)
        self.assertEqual(session_order, [0, 2, 1])
        self.assertEqual(path_order, [1, 2, 0])
        expected_scores = {
            i: round(
                1 / (RRF_K + session_order.index(i) + 1) + 1 / (RRF_K + path_order.index(i) + 1),
                9,
            )
            for i in pool
        }
        expected_order = sorted(
            pool,
            key=lambda i: (-expected_scores[i], -corpus._session_task[i], i),
        )
        ranked, scores = corpus._hybrid(3, pool)
        self.assertEqual(scores, expected_scores)
        self.assertEqual(ranked, expected_order)
        self.assertEqual(corpus.rank("hybrid", 3), expected_order)

    def test_resolved_only_keeps_hybrid_order(self):
        corpus = self.make_corpus()
        hybrid_order = corpus.rank("hybrid", 3)
        expected = [i for i in hybrid_order if corpus.sessions[i].resolved]
        self.assertEqual(corpus.rank("resolved_only", 3), expected)

    def test_fresh_decays_hybrid_scores_by_later_fixes(self):
        corpus = self.make_corpus()
        self.assertEqual([corpus.later_fixes(3, i) for i in range(3)], [1, 1, 0])
        pool = corpus.pool(3)
        _, hybrid_scores = corpus._hybrid(3, pool)
        expected_scores = {i: round(hybrid_scores[i] * STALE_DECAY ** corpus.later_fixes(3, i), 9) for i in pool}
        expected_order = sorted(
            pool,
            key=lambda i: (-expected_scores[i], -corpus._session_task[i], i),
        )
        self.assertEqual(corpus.rank("fresh", 3), expected_order)
        self.assertEqual(expected_order, [2, 1, 0])

    def test_unknown_retriever_name_raises_value_error(self):
        corpus = self.make_corpus()
        with self.assertRaises(ValueError):
            corpus.rank("missing", 3)


class MostFilesTest(unittest.TestCase):
    def test_orders_the_pool_by_number_of_files_named(self):
        tasks = [task(f"t-{i}", created_at=f"2024-01-0{i + 1} 00:00:00+00:00") for i in range(4)]
        sessions = [
            session("t-0", edited=("a.py",)),
            session("t-1", edited=("a.py",), opened=("b.py", "c.py")),
            session("t-2", opened=("a.py", "b.py")),
        ]
        self.assertEqual(Corpus(tasks, sessions).most_files(3), [1, 2, 0])


class PastFixFilesTest(unittest.TestCase):
    def test_counts_earlier_fixes_of_the_same_repo_then_prefers_the_latest(self):
        tasks = [
            task("a-0", "org/a", "2024-01-01 00:00:00+00:00", gold_files=("x.py", "y.py")),
            task("a-1", "org/a", "2024-01-02 00:00:00+00:00", gold_files=("x.py",)),
            task("a-2", "org/a", "2024-01-03 00:00:00+00:00", gold_files=("z.py",)),
            task("b-0", "org/b", "2024-01-01 00:00:00+00:00", gold_files=("w.py",)),
            task("a-q", "org/a", "2024-01-04 00:00:00+00:00", gold_files=("q.py",)),
            task("a-9", "org/a", "2024-01-05 00:00:00+00:00", gold_files=("later.py",)),
        ]
        self.assertEqual(Corpus(tasks, []).past_fix_files(4), ["x.py", "z.py", "y.py"])

    def test_ties_on_count_and_date_go_by_path(self):
        tasks = [
            task("a-0", created_at="2024-01-01 00:00:00+00:00", gold_files=("b.py", "a.py")),
            task("a-q", created_at="2024-01-02 00:00:00+00:00"),
        ]
        self.assertEqual(Corpus(tasks, []).past_fix_files(1), ["a.py", "b.py"])


class IssueTasksTest(unittest.TestCase):
    def test_ranks_strictly_earlier_same_repo_tasks_by_issue_text(self):
        tasks = [
            task("a-0", "org/a", "2024-01-01 00:00:00+00:00", issue="parser crash on unicode"),
            task("a-1", "org/a", "2024-01-02 00:00:00+00:00", issue="cache miss"),
            task("a-2", "org/a", "2024-01-03 00:00:00+00:00", issue="cache miss again"),
            task("b-0", "org/b", "2024-01-01 00:00:00+00:00", issue="parser crash"),
            task("a-q", "org/a", "2024-01-04 00:00:00+00:00", issue="parser crash"),
            task("a-9", "org/a", "2024-01-05 00:00:00+00:00", issue="parser crash"),
        ]
        self.assertEqual(Corpus(tasks, []).issue_tasks(4), [0, 2, 1])
