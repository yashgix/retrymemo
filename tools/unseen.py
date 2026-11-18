"""Score the frozen v3 rankers once on tasks the project never saw (SPEC-v3.md).

Set A: the pinned SWE-bench-extra rows of the eight repos that have no trajectory, so they are not in
data/tasks.jsonl. Their fixes join the history of later queries, as a repo's merged fixes would. Set B (`--expand`):
repos outside the eight with 10 to 19 tasks that have a pinned trajectory, sessions picked by v1's rule. `--count`
only counts the valid queries, without reading any gold file into a score.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recallbench.data import Session, Task, load  # noqa: E402
from recallbench.evaluate import file_hit  # noqa: E402
from recallbench.retrieve import MIN_HISTORY, Corpus  # noqa: E402
from recallbench.stats import BOOTSTRAP_DRAWS, SEED, intervals, r6  # noqa: E402
from recallbench.unseen import COMPARISONS, RANKERS, History  # noqa: E402
from tools.fetch_data import (  # noqa: E402
    DATA,
    MIN_COVERED,
    TASK_COLUMNS,
    download,
    picked_rows,
    read_table,
    select,
    session_record,
    task_record,
)

NS = (1, 5, 10)


def manifest() -> dict:
    return json.loads((DATA / "MANIFEST.json").read_text())


def extra_tasks(source: Path) -> list[Task]:
    """Every pinned SWE-bench-extra task that changes at least one file, built as tools/fetch_data.py builds them."""
    records = map(task_record, read_table(download(manifest()["tasks_source"], source), TASK_COLUMNS))
    return [
        Task(r["instance_id"], r["repo"], r["created_at"], r["license"], r["issue"], tuple(r["gold_files"]))
        for r in records
        if r["gold_files"]
    ]


def other_repo_sessions(source: Path, repo_of: dict[str, str], skip: set[str]) -> tuple[set[str], list[Session]]:
    """Set B repos and their sessions, by v1's pick rule limited to repos outside `skip` with 10 to 19 covered tasks."""
    shards = download(manifest()["sessions_source"], source / "SWE-agent-trajectories")
    index = [(r["instance_id"], r["target"]) for r in read_table(shards, ("instance_id", "target"))]
    repos, picked = select(index, {i: r for i, r in repo_of.items() if r not in skip}, MIN_HISTORY, MIN_COVERED)
    rows = picked_rows(shards, picked)
    return set(repos), [session(session_record(rows[r], r)) for r in picked]


def session(record: dict) -> Session:
    return Session(**{k: tuple(v) if isinstance(v, list) else v for k, v in record.items()})


def unseen_queries(tasks: list[Task], new_ids: set[str], covered: set[str]) -> list[int]:
    """Unseen tasks with at least MIN_HISTORY earlier same-repo tasks that have a session."""
    out = []
    for q, task in enumerate(tasks):
        earlier = sum(
            1 for t in tasks if t.repo == task.repo and t.created_at < task.created_at and t.instance_id in covered
        )
        if task.instance_id in new_ids and earlier >= MIN_HISTORY:
            out.append(q)
    return out


def build(source: Path, expand: bool) -> tuple[Corpus, dict[str, list[int]]]:
    """The corpus of seen and unseen tasks, and the valid unseen queries of set A and, with `expand`, set B."""
    tasks, sessions = load()
    known, eight = {t.instance_id for t in tasks}, {t.repo for t in tasks}
    extra = extra_tasks(source)
    new = [t for t in extra if t.repo in eight and t.instance_id not in known]
    other: set[str] = set()
    if expand:
        other, more = other_repo_sessions(source, {t.instance_id: t.repo for t in extra}, eight)
        new += [t for t in extra if t.repo in other]
        sessions = sessions + more
    everything = sorted(tasks + new, key=lambda t: (t.repo, t.created_at, t.instance_id))
    queries = unseen_queries(everything, {t.instance_id for t in new}, {s.instance_id for s in sessions})
    sets = {"A": [q for q in queries if everything[q].repo in eight]}
    if expand:
        sets["B"] = [q for q in queries if everything[q].repo in other]
        sets["all"] = queries
    return Corpus(everything, sessions), sets


def score(corpus: Corpus, queries: list[int]) -> dict:
    history = History(corpus)
    hits = {name: np.zeros((len(queries), len(NS))) for name in RANKERS}
    for row, q in enumerate(queries):
        gold = corpus.tasks[q].gold_files
        for name, files in history.file_lists(q).items():
            for c, n in enumerate(NS):
                hits[name][row, c] = file_hit(files[:n], gold)
    repo_names = [corpus.tasks[q].repo for q in queries]
    repos = sorted(set(repo_names))
    repo_of = np.array([repos.index(r) for r in repo_names], dtype=int)
    main = NS.index(5)

    comparisons = []
    for a, b in COMPARISONS:
        diff = hits[a][:, main] - hits[b][:, main]
        comparisons.append({"a": a, "b": b, "diff": r6(diff.mean())} | intervals(diff, repo_of, len(repos)))
    return {
        "queries": len(queries),
        "repos": repos,
        "repo_queries": [int((repo_of == r).sum()) for r in range(len(repos))],
        "ns": list(NS),
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "seed": SEED,
        "file_hit": {name: [r6(v) for v in hits[name].mean(axis=0)] for name in RANKERS},
        "file_hit5_ci": {name: intervals(hits[name][:, main], repo_of, len(repos)) for name in RANKERS},
        "repo_file_hit5": {
            name: [r6(hits[name][repo_of == r, main].mean()) for r in range(len(repos))] for name in RANKERS
        },
        "comparisons": comparisons,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="folder for the pinned parquet files")
    parser.add_argument("--expand", action="store_true", help="add set B from the pinned trajectory files")
    parser.add_argument("--count", action="store_true", help="only count the valid unseen queries per repo")
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "unseen.json")
    args = parser.parse_args()
    corpus, sets = build(args.source, args.expand)
    if args.count:
        for name, queries in sets.items():
            repos = Counter(corpus.tasks[q].repo for q in queries)
            print(
                json.dumps(
                    {"set": name, "queries": len(queries), "repos": len(repos), "per_repo": dict(sorted(repos.items()))}
                )
            )
        return
    result = {name: score(corpus, queries) for name, queries in sets.items()}
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
