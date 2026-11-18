"""File-level metrics (SPEC-v2): score the first N distinct files a ranking names, on the date split halves."""

import numpy as np

from .evaluate import file_hit, first_files, first_sources, gain, hit_at
from .retrieve import RETRIEVERS, Corpus
from .split import date_split
from .stats import BOOTSTRAP_DRAWS, SEED, draws, intervals, query_interval, r6
from .text import costs, tokens
from .vote import Fix, vote_files

NS = (1, 5, 10)
MAIN_N = 5
SESSION_RANKERS = (*RETRIEVERS, "most_files")
RANKERS = (*SESSION_RANKERS, "past_fix_files", "issue_fix_vote", "vote_prior")
COMPARISONS = (
    ("file_hit@5", "vote_prior", "issue_fix_vote"),
    ("file_hit@5", "vote_prior", "past_fix_files"),
    ("file_hit@5", "vote_prior", "hybrid"),
    ("file_hit@5", "hybrid", "past_fix_files"),
    ("file_hit@5", "hybrid", "bm25_issue"),
    ("file_hit@5", "hybrid", "most_files"),
    ("session_hit@5", "hybrid", "bm25_issue"),
    ("session_hit@5", "hybrid", "most_files"),
)
HALVES = ("test", "dev", "all")


class _Records:
    """Per-query arrays for every ranking, filled one query at a time."""

    def __init__(self, n_queries: int) -> None:
        def zeros(names, *shape):
            return {name: np.zeros((n_queries, *shape)) for name in names}

        self.hit = zeros(RANKERS, len(NS))
        self.recall = zeros(RANKERS, len(NS))
        self.found = zeros(RANKERS)
        self.list_tokens = zeros(RANKERS)
        self.card_tokens = zeros(SESSION_RANKERS)
        self.full_tokens = zeros(SESSION_RANKERS)
        self.session_hit5 = zeros(SESSION_RANKERS)
        self.named5 = zeros(SESSION_RANKERS)


def _file_lists(corpus: Corpus, q: int, rankings: dict[str, list[int]]) -> dict[str, list[str]]:
    sessions = corpus.sessions
    lists = {name: first_files([sessions[i].pointers for i in ranked], max(NS)) for name, ranked in rankings.items()}
    prior = corpus.past_fix_files(q)
    lists["past_fix_files"] = prior[: max(NS)]
    lists["vote_prior"] = vote_files([sessions[i] for i in rankings["hybrid"]], prior)[: max(NS)]
    fixes = [Fix(corpus.tasks[j].gold_files) for j in corpus.issue_tasks(q)]
    lists["issue_fix_vote"] = vote_files(fixes, prior)[: max(NS)]
    return lists


def _record(corpus: Corpus, q: int, j: int, records: _Records, cost: tuple[list[int], list[int]]) -> None:
    gold = corpus.tasks[q].gold_files
    rankings = {name: corpus.rank(name, q) for name in RETRIEVERS}
    rankings["most_files"] = corpus.most_files(q)
    for name, files in _file_lists(corpus, q, rankings).items():
        for c, n in enumerate(NS):
            records.hit[name][j, c] = file_hit(files[:n], gold)
            records.recall[name][j, c] = gain(files[:n], gold)
        records.found[name][j] = len(set(files[:MAIN_N]) & set(gold))
        records.list_tokens[name][j] = tokens("\n".join(files[:MAIN_N]))
    full, cards = cost
    for name, ranked in rankings.items():
        pointers = [corpus.sessions[i].pointers for i in ranked]
        used = [ranked[p] for p in first_sources(pointers, MAIN_N)]
        records.card_tokens[name][j] = sum(cards[i] for i in used)
        records.full_tokens[name][j] = sum(full[i] for i in used)
        records.session_hit5[name][j] = hit_at(pointers, gold, 5)
        records.named5[name][j] = len(set().union(*pointers[:5]))


def _per_1k(found: np.ndarray, spent: np.ndarray) -> float:
    return r6(1000 * found.mean() / spent.mean()) if spent.mean() else 0.0


def _tokens(records: _Records, rows: np.ndarray) -> dict[str, dict[str, float]]:
    out = {}
    for name in RANKERS:
        found, listed = records.found[name][rows], records.list_tokens[name][rows]
        entry = {"found5": r6(found.mean()), "list_tokens": r6(listed.mean()), "list_per_1k": _per_1k(found, listed)}
        if name in SESSION_RANKERS:
            for form, spent in (("card", records.card_tokens[name][rows]), ("full", records.full_tokens[name][rows])):
                entry[f"{form}_tokens"] = r6(spent.mean())
                entry[f"{form}_per_1k"] = _per_1k(found, spent)
        out[name] = entry
    return out


def _metric(records: _Records, metric: str, name: str, rows: np.ndarray) -> np.ndarray:
    if metric == "session_hit@5":
        return records.session_hit5[name][rows]
    return records.hit[name][rows, NS.index(MAIN_N)]


def _summary(records: _Records, rows: np.ndarray, repo_names: list[str]) -> dict:
    repos = sorted({repo_names[j] for j in rows})
    repo_of = np.array([repos.index(repo_names[j]) for j in rows], dtype=int)
    out = {"queries": len(rows), "repos": repos, "repo_queries": [int((repo_of == r).sum()) for r in range(len(repos))]}
    if not len(rows):
        return out
    main = NS.index(MAIN_N)
    hits = {name: records.hit[name][rows] for name in RANKERS}
    out["file_hit"] = {name: [r6(v) for v in hits[name].mean(axis=0)] for name in RANKERS}
    out["file_recall"] = {name: [r6(v) for v in records.recall[name][rows].mean(axis=0)] for name in RANKERS}
    out["file_hit_main_ci"] = {name: intervals(hits[name][:, main], repo_of, len(repos)) for name in RANKERS}
    out["session_hit5"] = {name: r6(records.session_hit5[name][rows].mean()) for name in SESSION_RANKERS}
    out["files_named5"] = {name: r6(records.named5[name][rows].mean()) for name in SESSION_RANKERS}
    out["repo_file_hit_main"] = {
        name: [r6(hits[name][repo_of == r, main].mean()) for r in range(len(repos))] for name in RANKERS
    }
    out["macro_file_hit_main"] = {name: r6(np.mean(out["repo_file_hit_main"][name])) for name in RANKERS}
    out["tokens"] = _tokens(records, rows)
    out["comparisons"] = []
    for metric, a, b in COMPARISONS:
        diff = _metric(records, metric, a, rows) - _metric(records, metric, b, rows)
        out["comparisons"].append(
            {"metric": metric, "a": a, "b": b, "diff": r6(diff.mean())} | intervals(diff, repo_of, len(repos))
        )
    out["repo_main_comparison"] = _repo_comparison(records, rows, repo_of, len(repos))
    return out


def _repo_comparison(records: _Records, rows: np.ndarray, repo_of: np.ndarray, n_repos: int) -> dict:
    """The first comparison within each repository, with a query bootstrap inside that repository."""
    metric, a, b = COMPARISONS[0]
    diff = _metric(records, metric, a, rows) - _metric(records, metric, b, rows)
    parts = [diff[repo_of == r] for r in range(n_repos)]
    return {
        "metric": metric,
        "a": a,
        "b": b,
        "diff": [r6(part.mean()) for part in parts],
        "query_ci": [[r6(v) for v in query_interval(part, draws(len(part)))] for part in parts],
    }


def file_metrics(tasks, sessions) -> dict:
    corpus = Corpus(tasks, sessions)
    queries = corpus.queries()
    records = _Records(len(queries))
    cost = costs(tasks, sessions)
    for j, q in enumerate(queries):
        _record(corpus, q, j, records, cost)
    position = {q: j for j, q in enumerate(queries)}
    dev, test = date_split(tasks, queries)
    rows = {
        "all": np.arange(len(queries)),
        "dev": np.array([position[q] for q in dev], dtype=int),
        "test": np.array([position[q] for q in test], dtype=int),
    }
    repo_names = [tasks[q].repo for q in queries]
    out = {
        "ns": list(NS),
        "main_n": MAIN_N,
        "rankers": list(RANKERS),
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "seed": SEED,
        "repos": sorted({task.repo for task in tasks}),
    }
    out |= {half: _summary(records, rows[half], repo_names) for half in HALVES}
    dev_hit5 = out["dev"].get("session_hit5")
    out["dev_choice"] = max(RETRIEVERS, key=lambda name: dev_hit5[name]) if dev_hit5 else None
    return out
