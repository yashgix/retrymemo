"""Choose the vote retriever's settings and the v1-style best retriever on the development half only."""

import argparse
import itertools
import json
from dataclasses import asdict
from pathlib import Path

from recallbench.data import load
from recallbench.evaluate import file_hit, gain, hit_at
from recallbench.files import MAIN_N
from recallbench.retrieve import RETRIEVERS, Corpus
from recallbench.split import date_split
from recallbench.stats import r6
from recallbench.vote import VoteSettings, vote_files

GRID = [
    VoteSettings(depth, cap, edited, prior, k)
    for depth, cap, edited, prior, k in itertools.product(
        (5, 10, 20, 50), (3, 5, 10, 20, None), (1.0, 2.0), (0.2, 0.35, 0.5, 0.65, 0.8), (5, 60)
    )
]


def _score(cases, settings: VoteSettings) -> dict[str, float]:
    hits, recalls = 0.0, 0.0
    for ranked, prior, gold in cases:
        files = vote_files(ranked, prior, settings)[:MAIN_N]
        hits += file_hit(files, gold)
        recalls += gain(files, gold)
    return {"file_hit5": r6(hits / len(cases)), "file_recall5": r6(recalls / len(cases))}


def _v1_choice(corpus: Corpus, dev: list[int]) -> dict:
    pointers = [session.pointers for session in corpus.sessions]
    hit5 = {
        name: r6(
            sum(hit_at([pointers[i] for i in corpus.rank(name, q)], corpus.tasks[q].gold_files, 5) for q in dev)
            / len(dev)
        )
        for name in RETRIEVERS
    }
    return {"session_hit5": hit5, "best": max(RETRIEVERS, key=lambda name: hit5[name])}


def tune(tasks, sessions) -> dict:
    corpus = Corpus(tasks, sessions)
    dev, _ = date_split(tasks, corpus.queries())
    cases = [
        ([sessions[i] for i in corpus.rank("hybrid", q)], corpus.past_fix_files(q), tasks[q].gold_files) for q in dev
    ]
    grid = [{**asdict(settings), **_score(cases, settings)} for settings in GRID]
    # max keeps the first of equal rows, so ties go by grid order.
    chosen = max(grid, key=lambda row: (row["file_hit5"], row["file_recall5"]))
    return {"dev_queries": len(dev), "grid": grid, "chosen": chosen, "v1_choice": _v1_choice(corpus, dev)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("results/tuning.json"))
    args = parser.parse_args(argv)
    result = tune(*load())
    args.out.write_text(json.dumps(result, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"chosen": result["chosen"], "v1_choice": result["v1_choice"]}, indent=1))


if __name__ == "__main__":
    main()
