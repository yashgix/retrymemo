import math
from collections import Counter

K1 = 1.2
B = 0.75


def bm25(query: list[str], docs: list[Counter]) -> list[float]:
    if not docs:
        return []
    lengths = [sum(doc.values()) for doc in docs]
    avgdl = sum(lengths) / len(docs)
    if avgdl == 0:
        return [0.0] * len(docs)
    terms = list(dict.fromkeys(query))
    n = len(docs)
    idf = {}
    for term in terms:
        df = sum(1 for doc in docs if doc[term])
        idf[term] = math.log(1 + (n - df + 0.5) / (df + 0.5))
    scores = []
    for doc, length in zip(docs, lengths, strict=True):
        score = 0.0
        for term in terms:
            tf = doc[term]
            if tf:
                score += idf[term] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avgdl))
        scores.append(round(score, 9))
    return scores
