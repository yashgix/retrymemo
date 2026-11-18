"""Compute the recall benchmark metrics and write them as JSON."""

import argparse
import json
from pathlib import Path

from .data import DATA_DIR, load
from .evaluate import BUDGETS, hit_at, ndcg_at, pack, recall_at
from .files import file_metrics
from .retrieve import MIN_HISTORY, RETRIEVERS, Corpus
from .stats import BOOTSTRAP_DRAWS, SEED, draws, query_interval, r6
from .text import costs

FILES_NAME = "files.json"

__all__ = ["BOOTSTRAP_DRAWS", "FILES_NAME", "SEED", "main", "metrics"]


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _bootstrap(values, query_draws):
    if query_draws is None or not values:
        return 0.0, 0.0
    return query_interval(values, query_draws)


def metrics(tasks, sessions):
    corpus = Corpus(tasks, sessions)
    queries = corpus.queries()
    n_queries = len(queries)
    repos = sorted({task.repo for task in tasks})
    repo_at = {repo: i for i, repo in enumerate(repos)}

    pointers = [session.pointers for session in sessions]
    pointer_sets = [set(p) for p in pointers]
    full_costs, card_costs = costs(tasks, sessions)

    names = list(RETRIEVERS)
    hit1 = {name: [] for name in names}
    hit5 = {name: [] for name in names}
    recall5 = {name: [] for name in names}
    ndcg5 = {name: [] for name in names}
    full_tokens5 = {name: [] for name in names}
    card_tokens5 = {name: [] for name in names}
    full_budget = {name: [[] for _ in BUDGETS] for name in names}
    card_budget = {name: [[] for _ in BUDGETS] for name in names}
    query_repo = []
    answerable_flags = []
    pool_sizes = []

    for q in queries:
        task = tasks[q]
        gold = task.gold_files
        gold_set = set(gold)
        pool = corpus.pool(q)
        pool_sizes.append(len(pool))
        query_repo.append(repo_at[task.repo])
        answerable_flags.append(any(pointer_sets[i] & gold_set for i in pool))
        pool_pointers = [pointers[i] for i in pool]

        for name in names:
            ranked = corpus.rank(name, q)
            ranked_pointers = [pointers[i] for i in ranked]
            hit1[name].append(hit_at(ranked_pointers, gold, 1))
            hit5[name].append(hit_at(ranked_pointers, gold, 5))
            recall5[name].append(recall_at(ranked_pointers, gold, 5))
            ndcg5[name].append(ndcg_at(ranked_pointers, pool_pointers, gold, 5))

            full_ranked = [full_costs[i] for i in ranked]
            card_ranked = [card_costs[i] for i in ranked]
            full_tokens5[name].append(sum(full_ranked[:5]))
            card_tokens5[name].append(sum(card_ranked[:5]))

            for b, budget in enumerate(BUDGETS):
                kept = pack(full_ranked, budget)
                full_budget[name][b].append(int(any(pointer_sets[ranked[j]] & gold_set for j in kept)))
                kept = pack(card_ranked, budget)
                card_budget[name][b].append(int(any(pointer_sets[ranked[j]] & gold_set for j in kept)))

    query_draws = draws(n_queries) if n_queries else None

    hit5_means = [r6(_mean(hit5[name])) for name in names]
    best = max(range(len(names)), key=lambda i: hit5_means[i])

    out = {
        "tasks": len(tasks),
        "sessions": len(sessions),
        "resolved_sessions": sum(session.resolved for session in sessions),
        "repos": len(repos),
        "queries": n_queries,
        "answerable": sum(answerable_flags),
        "min_history": MIN_HISTORY,
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "mean_pool": r6(_mean(pool_sizes)),
        "mean_full_tokens": r6(_mean(full_costs)),
        "mean_card_tokens": r6(_mean(card_costs)),
        "best_retriever": best,
        "budgets": list(BUDGETS),
    }

    out["hit1"] = [r6(_mean(hit1[name])) for name in names]
    out["hit5"] = hit5_means
    out["recall5"] = [r6(_mean(recall5[name])) for name in names]
    out["ndcg5"] = [r6(_mean(ndcg5[name])) for name in names]
    out["full_tokens5"] = [r6(_mean(full_tokens5[name])) for name in names]
    out["card_tokens5"] = [r6(_mean(card_tokens5[name])) for name in names]

    out["hit5_lo"] = []
    out["hit5_hi"] = []
    out["hit5_vs_issue"] = []
    out["hit5_vs_issue_lo"] = []
    out["hit5_vs_issue_hi"] = []
    base_hit5 = hit5["bm25_issue"]
    for name in names:
        lo, hi = _bootstrap(hit5[name], query_draws)
        out["hit5_lo"].append(r6(lo))
        out["hit5_hi"].append(r6(hi))
        if name == "bm25_issue":
            out["hit5_vs_issue"].append(0.0)
            out["hit5_vs_issue_lo"].append(0.0)
            out["hit5_vs_issue_hi"].append(0.0)
        else:
            diff = [a - b for a, b in zip(hit5[name], base_hit5, strict=True)]
            out["hit5_vs_issue"].append(r6(_mean(diff)))
            lo, hi = _bootstrap(diff, query_draws)
            out["hit5_vs_issue_lo"].append(r6(lo))
            out["hit5_vs_issue_hi"].append(r6(hi))

    out["full_tokens_per_hit"] = [
        r6(out["full_tokens5"][i] / out["hit5"][i]) if out["hit5"][i] else 0.0 for i in range(len(names))
    ]
    out["card_tokens_per_hit"] = [
        r6(out["card_tokens5"][i] / out["hit5"][i]) if out["hit5"][i] else 0.0 for i in range(len(names))
    ]

    out["full_budget_hit"] = [r6(_mean(full_budget[name][b])) for name in names for b in range(len(BUDGETS))]
    out["card_budget_hit"] = [r6(_mean(card_budget[name][b])) for name in names for b in range(len(BUDGETS))]

    out["repo_queries"] = [sum(1 for r in query_repo if r == repo_i) for repo_i in range(len(repos))]
    out["repo_answerable"] = [
        sum(1 for r, a in zip(query_repo, answerable_flags, strict=True) if r == repo_i and a)
        for repo_i in range(len(repos))
    ]
    out["repo_hit5"] = []
    for name in names:
        for repo_i in range(len(repos)):
            values = [v for r, v in zip(query_repo, hit5[name], strict=True) if r == repo_i]
            out["repo_hit5"].append(r6(_mean(values)))
    return out


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, sort_keys=True, indent=1) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="results/metrics.json")
    parser.add_argument("--data", default=DATA_DIR)
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    if not (data_dir / "tasks.jsonl").is_file():
        parser.error(f"{data_dir} has no tasks.jsonl")

    tasks, sessions = load(data_dir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_json(out, metrics(tasks, sessions))
    _write_json(out.parent / FILES_NAME, file_metrics(tasks, sessions))
