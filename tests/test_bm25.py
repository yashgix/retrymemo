import math
import unittest
from collections import Counter

from recallbench.bm25 import K1, B, bm25


def expected(query, doc, docs):
    """The specification's BM25 formula, written out here independently of the implementation."""
    n = len(docs)
    avgdl = sum(sum(d.values()) for d in docs) / n
    length = sum(doc.values())
    total = 0.0
    for term in dict.fromkeys(query):
        tf = doc[term]
        if not tf:
            continue
        df = sum(1 for d in docs if d[term])
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        total += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avgdl))
    return round(total, 9)


class Bm25Test(unittest.TestCase):
    def test_constants(self):
        self.assertEqual((K1, B), (1.2, 0.75))

    def test_no_docs(self):
        self.assertEqual(bm25(["a"], []), [])

    def test_all_docs_empty(self):
        self.assertEqual(bm25(["a"], [Counter(), Counter()]), [0.0, 0.0])

    def test_document_without_a_query_term_scores_zero(self):
        docs = [Counter({"a": 1}), Counter({"b": 1})]
        self.assertEqual(bm25(["a"], docs)[1], 0.0)

    def test_single_term_in_one_of_two_docs(self):
        # N=2, df=1 so idf = ln(2); both docs have length 1 = avgdl, so the tf factor is 1.
        docs = [Counter({"a": 1}), Counter({"b": 1})]
        self.assertEqual(bm25(["a"], docs), [0.693147181, 0.0])
        self.assertNotEqual(bm25(["a"], docs)[0], math.log(2))

    def test_equal_length_docs_with_one_occurrence_score_the_idf(self):
        # N=2, df=2 so idf = ln(1.2); every doc has length 1 = avgdl, so the tf factor is 1.
        docs = [Counter({"a": 1}), Counter({"a": 1})]
        self.assertEqual(bm25(["a"], docs), [0.182321557, 0.182321557])

    def test_duplicate_query_terms_count_once(self):
        docs = [Counter({"a": 2, "b": 1}), Counter({"a": 1, "c": 3})]
        self.assertEqual(bm25(["a", "a", "b"], docs), bm25(["a", "b"], docs))

    def test_shorter_document_scores_higher(self):
        short, long = bm25(["a"], [Counter({"a": 1}), Counter({"a": 1, "b": 2})])
        self.assertGreater(short, long)

    def test_more_occurrences_score_higher(self):
        once, five = bm25(["a"], [Counter({"a": 1}), Counter({"a": 5})])
        self.assertGreater(five, once)

    def test_identical_documents_tie_exactly(self):
        docs = [Counter({"a": 2, "b": 1}), Counter({"a": 2, "b": 1})]
        first, second = bm25(["a", "b"], docs)
        self.assertEqual(first, second)

    def test_rounds_like_the_formula_on_a_mixed_pool(self):
        docs = [Counter({"a": 2, "b": 1}), Counter({"a": 1, "c": 3}), Counter({"d": 4})]
        query = ["a", "b", "z"]
        self.assertEqual(bm25(query, docs), [expected(query, doc, docs) for doc in docs])
