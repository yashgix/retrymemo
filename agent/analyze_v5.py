"""v5 analysis as pre-registered in SPEC-v5.md: per-task means over repeats, paired differences against arm A with
bootstrap intervals over tasks and over repositories, an exact McNemar test on solve rate, and the stalled subgroup.
"""

import argparse
import json
from math import comb
from pathlib import Path

import numpy as np

from agent.summarize_pilot import arm_summary, crashed
from recallbench.stats import intervals, r6

MEASURES = ("steps", "cost", "resolved")


def split_tasks(runs: list[dict], arms: list[str], repeats: int) -> tuple[list[str], list[str], list[str]]:
    """Tasks with every arm × repeat logged and no crash, then those with a crash, then those left incomplete."""
    wanted = {(arm, repeat) for arm in arms for repeat in range(1, repeats + 1)}
    by_task: dict[str, list[dict]] = {}
    for run in runs:
        by_task.setdefault(run["task"], []).append(run)
    keep, crash, incomplete = [], [], []
    for task, task_runs in sorted(by_task.items()):
        if any(crashed(r) for r in task_runs):
            crash.append(task)
        elif {(r["arm"], r["repeat"]) for r in task_runs} >= wanted:
            keep.append(task)
        else:
            incomplete.append(task)
    return keep, crash, incomplete


def mcnemar(only_first: int, only_second: int) -> float:
    """Exact two-sided McNemar p-value from the two kinds of disagreeing pairs."""
    n = only_first + only_second
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(only_first, only_second) + 1))
    return min(1.0, 2 * tail / 2**n)


def compare(base: np.ndarray, other: np.ndarray, repo_of: np.ndarray, n_repos: int) -> dict:
    diff = other - base
    out = {"base": r6(base.mean()), "other": r6(other.mean()), "diff": r6(diff.mean())}
    out["relative"] = r6(diff.mean() / base.mean()) if base.mean() else None
    out |= intervals(diff, repo_of, n_repos)
    out["clear"] = all(lo > 0 or hi < 0 for lo, hi in (out["query_ci"], out["repo_ci"]))
    return out


def task_means(runs: list[dict], tasks: list[str], arm: str, measure: str) -> np.ndarray:
    """One value per task: the measure averaged over that task's repeats in this arm."""
    return np.array([np.mean([float(r[measure]) for r in runs if r["task"] == t and r["arm"] == arm]) for t in tasks])


def disagreements(runs: list[dict], tasks: set[str], base: str, arm: str) -> tuple[int, int]:
    """Runs paired by task and repeat: how often only the base solved it, and how often only the other arm did."""
    solved = {(r["task"], r["arm"], r["repeat"]): r["resolved"] for r in runs if r["task"] in tasks}
    pairs = [(v, solved[(t, arm, k)]) for (t, a, k), v in solved.items() if a == base]
    return sum(a and not b for a, b in pairs), sum(b and not a for a, b in pairs)


def comparisons(runs: list[dict], tasks: list[str], arms: list[str], repo: dict[str, str]) -> dict:
    """Each arm against A, paired by task, each measure averaged over that task's repeats first."""
    repos = sorted({repo[t] for t in tasks})
    repo_of = np.array([repos.index(repo[t]) for t in tasks])
    base = arms[0]
    out = {"tasks": len(tasks), "repos": len(repos)}
    for arm in arms[1:]:
        measures = {
            m: compare(task_means(runs, tasks, base, m), task_means(runs, tasks, arm, m), repo_of, len(repos))
            for m in MEASURES
        }
        out[f"{arm}-{base}"] = measures | {"mcnemar_p": mcnemar(*disagreements(runs, set(tasks), base, arm))}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, nargs="+", required=True)
    parser.add_argument("--arms", default="A,B", help="comma-separated, the first is the base")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    arms = args.arms.split(",")
    info = {t["task"]: t for path in args.tasks for t in json.loads(path.read_text())["tasks"]}
    rows = [json.loads(line) for line in args.results.read_text().splitlines()]
    runs = [r | {"repeat": r.get("repeat", 1)} for r in rows if r["arm"] in arms]
    runs = [r for r in runs if r["repeat"] <= args.repeats]
    keep, crash, incomplete = split_tasks(runs, arms, args.repeats)
    kept = [r for r in runs if r["task"] in set(keep)]
    repo = {t: info[t]["repo"] for t in keep}
    stalled = [t for t in keep if info[t].get("stalled")]
    dev = [t for t in keep if info[t].get("split") == "dev"]
    summary = {
        "tasks_failed_validation": [r["task"] for r in rows if r["arm"] == "validation" and not r["ok"]],
        "tasks_left_out_crash": crash,
        "tasks_left_out_incomplete": incomplete,
        "train_fill_tasks": sum(info[t].get("split") == "train" for t in keep),
        "spend_logged_runs": round(sum(r["cost"] for r in runs), 4),
        "arms": {arm: arm_summary([r for r in kept if r["arm"] == arm]) for arm in arms},
        "all_tasks": comparisons(kept, keep, arms, repo) if keep else None,
        "dev_tasks_only": comparisons(kept, dev, arms, repo) if dev else None,
        "stalled_subgroup": comparisons(kept, stalled, arms, repo) if stalled else None,
    }
    args.out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "arms"}, indent=1))


if __name__ == "__main__":
    main()
