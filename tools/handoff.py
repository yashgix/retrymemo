"""Score the v4 retry handoff against the no-memory baseline on one split (SPEC-v4.md).

Reads the records tools/attempts.py writes and the pinned SWE-bench-extra tasks. The unit is the first retry of an
issue: its second attempt, after the first failed. One per task, so tasks stay independent.
"""

import argparse
import gzip
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recallbench.handoff import handoff, opened  # noqa: E402
from recallbench.retrieve import Corpus  # noqa: E402
from recallbench.stats import intervals, r6  # noqa: E402
from recallbench.unseen import History  # noqa: E402
from recallbench.vote import Fix, vote_files  # noqa: E402
from tools.unseen import extra_tasks  # noqa: E402

# retry_agent is the real second attempt, which had full code access and no memory: the files it opened, in order.
RANKERS = ("handoff", "retry_agent", "fix_hybrid")
COMPARISONS = (("handoff", "retry_agent"), ("handoff", "fix_hybrid"), ("retry_agent", "fix_hybrid"))


def first_retries(path: Path, split: str) -> list[tuple[dict, dict]]:
    """(first attempt, second attempt) per task of the split whose first attempt (lowest row) failed."""
    attempts: dict[str, list[dict]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if record["split"] == split:
                attempts.setdefault(record["instance_id"], []).append(record)
    pairs = []
    for task_id in sorted(attempts):
        ordered = sorted(attempts[task_id], key=lambda a: a["row"])
        if len(ordered) >= 2 and not ordered[0]["resolved"]:
            pairs.append((ordered[0], ordered[1]))
    return pairs


def fix_ranking(corpus: Corpus, history: History, q: int) -> list[str]:
    fixes = [Fix(corpus.tasks[j].gold_files) for j in history.fix_hybrid_tasks(q)]
    return vote_files(fixes, corpus.past_fix_files(q))


def lists(corpus: Corpus, history: History, q: int, failed: dict, retry: dict, weight: float) -> dict[str, list[str]]:
    fix = fix_ranking(corpus, history, q)
    return {"handoff": handoff([failed], fix, weight), "retry_agent": opened(retry), "fix_hybrid": fix}


def first_needed_step(retry: dict, gold: set[str]) -> int | None:
    steps = [step for path, step in retry["first_open"].items() if path in gold]
    return min(steps) if steps else None


def score(source: Path, attempts: Path, split: str, edited_weight: float) -> dict:
    tasks = sorted(extra_tasks(source), key=lambda t: (t.repo, t.created_at, t.instance_id))
    corpus = Corpus(tasks, [])
    history = History(corpus)
    index = {t.instance_id: q for q, t in enumerate(tasks)}
    pairs = [(a, b) for a, b in first_retries(attempts, split) if a["instance_id"] in index]
    hit1 = {name: np.zeros(len(pairs)) for name in RANKERS}
    hit5 = {name: np.zeros(len(pairs)) for name in RANKERS}
    reach, examples = [], []
    for row, (failed, retry) in enumerate(pairs):
        q = index[failed["instance_id"]]
        gold = set(tasks[q].gold_files)
        ranked = lists(corpus, history, q, failed, retry, edited_weight)
        for name, files in ranked.items():
            hit1[name][row] = bool(gold & set(files[:1]))
            hit5[name][row] = bool(gold & set(files[:5]))
        reach.append(first_needed_step(retry, gold))
        top = {name: files[:3] for name, files in ranked.items()}
        examples.append(
            {"task": failed["instance_id"], "gold": sorted(gold), "retry_first_needed_step": reach[-1], "top": top}
        )
    repo_names = [tasks[index[a["instance_id"]]].repo for a, _ in pairs]
    return summary(repo_names, hit1, hit5, reach, pairs) | {"examples": examples}


def summary(repo_names, hit1, hit5, reach, pairs) -> dict:
    repos = sorted(set(repo_names))
    repo_of = np.array([repos.index(r) for r in repo_names], dtype=int)

    missed = np.array([step is None for step in reach])
    reached = [step for step in reach if step is not None]
    out = {
        "tasks": len(pairs),
        "repos": len(repos),
        "retry_resolved": int(sum(b["resolved"] for _, b in pairs)),
        "retry_steps_median": float(np.median([b["steps"] for _, b in pairs])),
        "retry_never_opened_needed_file": int(missed.sum()),
        "retry_first_needed_step_median": float(np.median(reached)) if reached else None,
    }
    for name in RANKERS:
        out[name] = {"hit1": r6(hit1[name].mean()), "hit5": r6(hit5[name].mean())}
        out[name] |= {
            "hit1_ci": intervals(hit1[name], repo_of, len(repos)),
            "hit5_ci": intervals(hit5[name], repo_of, len(repos)),
        }
        out[name]["hit1_when_retry_never_opened"] = r6(hit1[name][missed].mean()) if missed.any() else None
    h, r = hit1["handoff"].astype(bool), hit1["retry_agent"].astype(bool)
    out["hit1_handoff_right_retry_wrong"] = int((h & ~r).sum())
    out["hit1_handoff_wrong_retry_right"] = int((~h & r).sum())
    for a, b in COMPARISONS:
        for metric, values in (("hit1", hit1), ("hit5", hit5)):
            diff = values[a] - values[b]
            out[f"{metric}:{a}-{b}"] = {"diff": r6(diff.mean())} | intervals(diff, repo_of, len(repos))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="folder with the pinned SWE-bench-extra parquet")
    parser.add_argument("--attempts", type=Path, required=True, help="tools/attempts.py output")
    parser.add_argument("--split", choices=("train", "dev", "test"), required=True)
    parser.add_argument("--edited-weight", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.source, args.attempts, args.split, args.edited_weight)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "examples"}, indent=1))


if __name__ == "__main__":
    main()
