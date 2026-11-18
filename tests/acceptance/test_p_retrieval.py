import math
import unittest
from collections import Counter

from recallbench.bm25 import K1, B, bm25
from recallbench.data import Session, Task
from recallbench.retrieve import MIN_HISTORY, RETRIEVERS, RRF_K, STALE_DECAY, Corpus
from recallbench.text import card, session_text, title, tokens, words


def task(i, issue="", gold=("a.py",), repo="o/r", day=None):
    day = i + 1 if day is None else day
    return Task(f"t-{i}", repo, f"2020-01-{day:02d} 00:00:00+00:00", "mit", issue, tuple(gold))


def session(i, resolved=False, opened=(), edited=(), turns=()):
    return Session(f"t-{i}", 100 + i, "m", resolved, "submitted", tuple(opened), tuple(edited), tuple(turns))


def rrf(order_a, order_b):
    ra = {m: r for r, m in enumerate(order_a, 1)}
    rb = {m: r for r, m in enumerate(order_b, 1)}
    return {m: round(1 / (RRF_K + ra[m]) + 1 / (RRF_K + rb[m]), 9) for m in ra}


def ordered(corpus, scores):
    return sorted(scores, key=lambda m: (-scores[m], -corpus.tasks.index(task_of(corpus, m)), m))


def task_of(corpus, m):
    return next(t for t in corpus.tasks if t.instance_id == corpus.sessions[m].instance_id)


class Text(unittest.TestCase):
    def test_words(self):
        self.assertEqual(
            words("BaseModel.parse_obj() in dvc/repo/index.py, x"),
            ["base", "model", "parse", "obj", "in", "dvc", "repo", "index", "py"],
        )
        self.assertEqual(words("getHTTPResponse a ab AB2c"), ["get", "httpresponse", "ab", "ab2c"])
        self.assertEqual(words(""), [])

    def test_tokens(self):
        self.assertEqual([tokens("x" * n) for n in (0, 1, 4, 5, 8, 9)], [0, 1, 1, 2, 2, 3])

    def test_forms(self):
        t = task(0, issue="\n  Crash on save  \nbody text")
        s = session(0, resolved=True, opened=("a.py", "b.py"), edited=("a.py",), turns=("look", "edit"))
        self.assertEqual(title(t), "Crash on save")
        self.assertEqual(title(task(1, issue="  ")), "")
        self.assertEqual(session_text(t, s), t.issue + "\nlook\nedit")
        self.assertEqual(card(t, s), "Crash on save\nresolved: yes\nedited: a.py\nopened: a.py, b.py")
        self.assertEqual(card(t, session(0)), "Crash on save\nresolved: no\nedited: \nopened: ")


class Bm25(unittest.TestCase):
    def test_exact_scores(self):
        self.assertEqual((K1, B), (1.2, 0.75))
        docs = [Counter({"a": 2, "b": 1}), Counter({"b": 1, "c": 3}), Counter()]
        avgdl, n = 7 / 3, 3
        idf_a = math.log(1 + (n - 1 + 0.5) / (1 + 0.5))
        idf_c = idf_a

        def part(idf, tf, length):
            return idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avgdl))

        got = bm25(["a", "c", "a", "zz"], docs)
        self.assertIsInstance(got, list)
        self.assertEqual(got, [round(part(idf_a, 2, 3), 9), round(part(idf_c, 3, 4), 9), 0.0])

    def test_edge_cases(self):
        self.assertEqual(bm25(["a"], []), [])
        self.assertEqual(bm25(["a"], [Counter(), Counter()]), [0.0, 0.0])
        common = bm25(["a"], [Counter({"a": 1}), Counter({"a": 1})])
        self.assertTrue(all(s > 0 for s in common))


class Pools(unittest.TestCase):
    def setUp(self):
        tasks = [task(0), task(1), task(2, repo="o/s", day=1), task(3, day=2), task(4)]
        sessions = [session(0, resolved=True), session(0), session(1), session(2), session(3), session(4)]
        self.corpus = Corpus(tasks, sessions)

    def test_pool_is_earlier_same_repo(self):
        self.assertEqual(self.corpus.pool(4), [0, 1, 2, 4])
        self.assertEqual(self.corpus.pool(1), [0, 1])
        self.assertEqual(self.corpus.pool(3), [0, 1])
        self.assertEqual(self.corpus.pool(2), [])
        self.assertEqual(self.corpus.pool(0), [])

    def test_recent_order(self):
        self.assertEqual(self.corpus.rank("recent", 4), [4, 2, 0, 1])

    def test_queries(self):
        self.assertEqual(MIN_HISTORY, 10)
        tasks = [task(i) for i in range(12)] + [task(12, day=11), task(13, repo="o/s", day=20)]
        corpus = Corpus(tasks, [session(i) for i in range(14)])
        self.assertEqual(corpus.queries(), [10, 11, 12])

    def test_unknown_retriever(self):
        with self.assertRaises(ValueError):
            self.corpus.rank("bm25", 4)


class Retrievers(unittest.TestCase):
    def setUp(self):
        tasks = [
            task(0, issue="parser crash with tokenizer", gold=("src/parser.py",)),
            task(1, issue="docs typo", gold=("README.md",)),
            task(2, issue="slow import", gold=("src/tokenizer.py",)),
            task(3, issue="unrelated thing", gold=("src/tokenizer.py",)),
            task(4, issue="tokenizer crash in parser", gold=("src/tokenizer.py",)),
        ]
        sessions = [
            session(0, resolved=True, opened=("docs/index.md",)),
            session(1, turns=("the tokenizer and parser code, tokenizer again",)),
            session(2, resolved=True, opened=("src/tokenizer.py",), edited=("src/tokenizer.py",)),
            session(3, opened=("setup.py",)),
        ]
        self.corpus = Corpus(tasks, sessions)

    def test_constants(self):
        self.assertEqual(
            RETRIEVERS, ("recent", "bm25_issue", "bm25_session", "bm25_paths", "hybrid", "resolved_only", "fresh")
        )
        self.assertEqual((RRF_K, STALE_DECAY), (60, 0.8))

    def test_each_text_source(self):
        c = self.corpus
        self.assertEqual(c.rank("bm25_issue", 4), [0, 3, 2, 1])
        self.assertEqual(c.rank("bm25_session", 4), [0, 1, 3, 2])
        self.assertEqual(c.rank("bm25_paths", 4), [2, 3, 1, 0])

    def test_hybrid_is_rank_fusion(self):
        c = self.corpus
        fused = rrf(c.rank("bm25_session", 4), c.rank("bm25_paths", 4))
        self.assertEqual(c.rank("hybrid", 4), ordered(c, fused))

    def test_resolved_only(self):
        c = self.corpus
        self.assertEqual(c.rank("resolved_only", 4), [m for m in c.rank("hybrid", 4) if c.sessions[m].resolved])

    def test_later_fixes_and_fresh(self):
        c = self.corpus
        self.assertEqual([c.later_fixes(4, m) for m in range(4)], [0, 0, 1, 0])
        self.assertEqual(c.later_fixes(3, 2), 0)
        fused = rrf(c.rank("bm25_session", 4), c.rank("bm25_paths", 4))
        decayed = {m: round(h * STALE_DECAY ** c.later_fixes(4, m), 9) for m, h in fused.items()}
        self.assertEqual(c.rank("fresh", 4), ordered(c, decayed))


class Stats(unittest.TestCase):
    def test_statistics_come_from_the_pool(self):
        issues = ["xray", "yak", "yak zebra", "xray yak", "xray other", "xray"]
        tasks = [task(i, issue=text) for i, text in enumerate(issues)]
        c = Corpus(tasks, [session(i) for i in range(6)])
        scores = bm25(words("xray yak"), [Counter(words(t)) for t in issues[:3]])
        self.assertEqual(c.rank("bm25_issue", 3), ordered(c, dict(enumerate(scores))))
        self.assertEqual(c.rank("bm25_issue", 3), [0, 1, 2])
