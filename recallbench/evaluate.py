import math

KS = (1, 5)
BUDGETS = (500, 1000, 2000, 4000, 8000, 16000)
PACK_DEPTH = 20


def gain(pointers, gold) -> float:
    gold_files = set(gold)
    if not gold_files:
        return 0.0
    return len(set(pointers) & gold_files) / len(gold_files)


def _new_files(ranked, n: int):
    """(position, path) for each file not seen before, walking the ranked memories in order, until n files."""
    seen = set()
    for position, pointers in enumerate(ranked):
        for path in pointers:
            if len(seen) == n:
                return
            if path not in seen:
                seen.add(path)
                yield position, path


def first_files(ranked, n: int) -> list[str]:
    """The first n distinct files named when walking the ranked memories' pointers in order."""
    return [path for _, path in _new_files(ranked, n)]


def first_sources(ranked, n: int) -> list[int]:
    """Positions of the memories that supply at least one of those first n files."""
    return list(dict.fromkeys(position for position, _ in _new_files(ranked, n)))


def file_hit(files, gold) -> int:
    return int(bool(set(files) & set(gold)))


def hit_at(ranked, gold, k: int) -> int:
    gold_files = set(gold)
    return int(any(gold_files.intersection(pointers) for pointers in ranked[:k]))


def recall_at(ranked, gold, k: int) -> float:
    gold_files = set(gold)
    if not gold_files:
        return 0.0
    found = set()
    for pointers in ranked[:k]:
        found.update(pointers)
    return len(found & gold_files) / len(gold_files)


def ndcg_at(ranked, pool, gold, k: int) -> float:
    dcg = sum(gain(pointers, gold) / math.log2(position + 1) for position, pointers in enumerate(ranked[:k], 1))
    ideal = sorted((gain(pointers, gold) for pointers in pool), reverse=True)[:k]
    ideal_dcg = sum(value / math.log2(position + 1) for position, value in enumerate(ideal, 1))
    return dcg / ideal_dcg if ideal_dcg else 0.0


def pack(costs, budget: int, depth: int = PACK_DEPTH) -> list[int]:
    kept = []
    remaining = budget
    for position, cost in enumerate(costs[:depth]):
        if cost <= remaining:
            kept.append(position)
            remaining -= cost
    return kept
