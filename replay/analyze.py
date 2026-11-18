"""The pre-registered analysis of the replay memory experiment (replay/ANALYSIS.md). It refuses to run when a frozen
input changed (replay/FROZEN.json).

Usage: python3 -m replay.analyze --runs ~/replay-work/paid-run
"""

import argparse
import hashlib
import itertools
import json
import math
import random
import statistics
from pathlib import Path

from replay import ARMS, RESULTS, ROOT, SEEDS
from replay.freeze import check
from replay.scoring import infrastructure_failure
from replay.tasks import NAMES

PRIMARY_TASKS = ("lock", "verifier", "onnx", "fczero")
SEPARATE_TASKS = ("simlink",)
# Rule: the repo at the task's start already applies the lesson's pattern somewhere (lock: a FOR NO KEY UPDATE
# comment in src/feedback/apply.ts, verifier: a lock query inside try with release in finally in src/jobs/rate-limit.ts,
# onnx: the full shape validation of the logistic model in src/match/model.ts). fczero's repo has no such code.
LESSON_IN_REPO = {"lock": True, "verifier": True, "onnx": True, "fczero": False}
PRIMARY = (("oracle", "none"), ("retrieved", "none"), ("oracle", "random"))
SECONDARY = (*PRIMARY, ("instructions", "none"))
MIN_COMPLETE_SEEDS = 3
BOOTSTRAP = 10_000
BOOTSTRAP_SEED = 20261117
TOKENS = ("input", "cached_input", "output")
PROCESS = (*TOKENS, "steps", "seconds", "cost")


def counted(runs: list[dict]) -> tuple[dict, list[dict]]:
    """Counted runs keyed by (task, arm, seed), and the infrastructure failures (missing data)."""
    kept = {}
    for r in (r for r in runs if not infrastructure_failure(r)):
        key = (r["task"], r["arm"], r["seed"])
        if key in kept:
            raise ValueError(f"two counted runs for {key}")
        kept[key] = r
    return kept, [r for r in runs if infrastructure_failure(r)]


def complete_seeds(kept: dict) -> list[int]:
    return [s for s in range(1, SEEDS + 1) if all((t, a, s) in kept for t in NAMES for a in ARMS)]


def rate(kept: dict, task: str, arm: str, seeds: list[int], outcome: str) -> list[bool]:
    return [bool(kept[(task, arm, s)][outcome]) for s in seeds if (task, arm, s) in kept]


def wilson(successes: int, n: int, z: float = 1.959964) -> list[float] | None:
    if n == 0:
        return None
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def sign_flip_p(diffs: list[float]) -> float:
    """Exact two-sided p: the share of all sign patterns whose absolute mean is at least the observed one."""
    observed = abs(statistics.mean(diffs))
    patterns = list(itertools.product((1, -1), repeat=len(diffs)))
    extreme = sum(
        abs(statistics.mean(s * d for s, d in zip(signs, diffs, strict=True))) >= observed - 1e-12 for signs in patterns
    )
    return extreme / len(patterns)


def holm(pvalues: list[float]) -> list[float]:
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted, running = [0.0] * len(pvalues), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvalues) - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def bootstrap_ci(per_task: dict[str, tuple[list[bool], list[bool]]]) -> list[float]:
    """95% interval of the mean difference: resample tasks, then each arm's seeds within a task."""
    rng = random.Random(BOOTSTRAP_SEED)
    tasks = sorted(per_task)
    means = []
    for _ in range(BOOTSTRAP):
        diffs = []
        for task in rng.choices(tasks, k=len(tasks)):
            a, b = per_task[task]
            diffs.append(statistics.mean(rng.choices(a, k=len(a))) - statistics.mean(rng.choices(b, k=len(b))))
        means.append(statistics.mean(diffs))
    means.sort()
    return [round(means[int(0.025 * BOOTSTRAP)], 4), round(means[int(0.975 * BOOTSTRAP) - 1], 4)]


def reading(diffs: list[float], interval: list[float]) -> str:
    if sum(d > 0 for d in diffs) >= 3 and interval[0] > 0:
        return "consistent benefit"
    if sum(d < 0 for d in diffs) >= 3 and interval[1] < 0:
        return "consistent harm"
    return "no clear effect"


def compare(kept: dict, tasks: tuple, seeds: list[int], arms: tuple[str, str], outcome: str, test: bool) -> dict:
    per_task = {t: (rate(kept, t, arms[0], seeds, outcome), rate(kept, t, arms[1], seeds, outcome)) for t in tasks}
    per_task = {t: v for t, v in per_task.items() if v[0] and v[1]}
    diffs = {t: statistics.mean(a) - statistics.mean(b) for t, (a, b) in per_task.items()}
    out = {
        "tasks": {
            t: {
                "rates": [round(statistics.mean(a), 4), round(statistics.mean(b), 4)],
                "wilson": [wilson(sum(a), len(a)), wilson(sum(b), len(b))],
                "n": [len(a), len(b)],
                "difference": round(diffs[t], 4),
            }
            for t, (a, b) in per_task.items()
        },
        "mean_difference": round(statistics.mean(diffs.values()), 4) if diffs else None,
    }
    if test and len(diffs) == len(tasks):
        interval = bootstrap_ci(per_task)
        out |= {"p": sign_flip_p(list(diffs.values())), "bootstrap_95": interval}
        out["reading"] = reading(list(diffs.values()), interval)
    return out


def comparisons(kept: dict, seeds: list[int], tasks: tuple, outcome: str, pairs: tuple, test: bool) -> dict:
    found = {f"{a} vs {b}": compare(kept, tasks, seeds, (a, b), outcome, test) for a, b in pairs}
    tested = [k for k, v in found.items() if "p" in v]
    for key, adjusted in zip(tested, holm([found[k]["p"] for k in tested]), strict=True):
        found[key]["p_holm"] = round(adjusted, 4)
    return found


def summary(rows: list[dict]) -> dict:
    """Mean and median of tokens, steps, seconds and cost."""
    values = {k: [r["tokens"].get(k, 0) if k in TOKENS else r[k] for r in rows] for k in PROCESS}
    return {
        k: {"mean": round(statistics.mean(v), 4), "median": statistics.median(v)} if v else None
        for k, v in values.items()
    }


def process(runs: list[dict]) -> dict:
    """Per condition overall, and per task and condition."""
    out = {"overall": {arm: summary([r for r in runs if r["arm"] == arm]) for arm in ARMS}}
    for task in NAMES:
        out[task] = {arm: summary([r for r in runs if r["arm"] == arm and r["task"] == task]) for arm in ARMS}
    return out


def retrieval(manifest: dict) -> dict:
    return {
        t: {
            "gold_bm25_ranks": manifest[t]["gold_ranks"],
            "gold_in_retrieved_text": len(manifest[t]["texts"]["retrieved-1"]["gold_items"]),
        }
        for t in NAMES
    }


def analyze(runs: list[dict], manifest: dict) -> dict:
    kept, failures = counted(runs)
    seeds = complete_seeds(kept)
    every = sorted({s for (_, _, s) in kept})
    in_repo = tuple(t for t in PRIMARY_TASKS if LESSON_IN_REPO[t])
    not_in_repo = tuple(t for t in PRIMARY_TASKS if not LESSON_IN_REPO[t])
    result = {
        "runs": len(runs),
        "counted": len(kept),
        "complete_seeds": seeds,
        "missing": [
            {k: r[k] for k in ("task", "arm", "seed", "exit_status")} | {"scoring_error": r.get("scoring_error")}
            for r in failures
        ],
        "enough_data": len(seeds) >= MIN_COMPLETE_SEEDS,
        "primary": comparisons(kept, seeds, PRIMARY_TASKS, "avoided", PRIMARY, test=True),
        "secondary_pass": comparisons(kept, seeds, PRIMARY_TASKS, "pass", SECONDARY, test=False),
        "instructions_avoided": comparisons(kept, seeds, PRIMARY_TASKS, "avoided", SECONDARY[3:], test=False),
        "simlink": comparisons(kept, seeds, SEPARATE_TASKS, "avoided", SECONDARY, test=False),
        "lesson_in_repo": comparisons(kept, seeds, in_repo, "avoided", PRIMARY, test=False),
        "lesson_not_in_repo": comparisons(kept, seeds, not_in_repo, "avoided", PRIMARY, test=False),
        "sensitivity_all_counted": comparisons(kept, every, PRIMARY_TASKS, "avoided", PRIMARY, test=True),
        "retrieval": retrieval(manifest),
        "process": process(list(kept.values())),
        "total_cost": round(sum(r["cost"] for r in runs), 6),
        "calls_without_usage": sum(r["tokens"].get("calls_without_usage", 0) for r in runs),
    }
    if not result["enough_data"]:
        for row in result["primary"].values():
            row["reading"] = "too little data"
    return result


def cost_check(folder: Path, total: float) -> dict:
    """The summed run costs against the runner's own total and DeepSeek's balance (cent steps, updated late)."""
    spend = folder / "spend.json"
    receipts = [json.loads(p.read_text()) for p in sorted(folder.glob("balance-*.json"))]
    drop = sum(r["before"] - r["after"] for r in receipts if "after" in r)
    return {
        "runs_total": total,
        "spend_json": json.loads(spend.read_text())["total"] if spend.exists() else None,
        "balance_drop": round(drop, 6),
        "balance_receipts": receipts,
    }


def provenance_problems(runs: list[dict], freeze_id: str) -> list[str]:
    """Only runs made under this freeze count: the excluded first run and any older run carry no or another id."""
    return [
        f"{r['task']}.{r['arm']}.{r['seed']}: made under another freeze" for r in runs if r.get("freeze") != freeze_id
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True, help="the runner's --out folder")
    args = parser.parse_args()
    if problems := check():
        raise SystemExit("frozen inputs changed, refusing to analyze:\n" + "\n".join(problems))
    runs = [json.loads(line) for line in (args.runs / "results.jsonl").read_text().splitlines()]
    if problems := provenance_problems(runs, hashlib.sha256((ROOT / "FROZEN.json").read_bytes()).hexdigest()):
        raise SystemExit("refusing runs from another freeze:\n" + "\n".join(problems[:10]))
    manifest = json.loads((ROOT / "memory" / "manifest.json").read_text())
    result = analyze(runs, manifest)
    result["cost_check"] = cost_check(args.runs, result["total_cost"])
    (RESULTS / "replay-analysis.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: result[k] for k in ("runs", "counted", "complete_seeds", "enough_data", "total_cost")}))
    for name, row in result["primary"].items():
        print(name, row.get("mean_difference"), row.get("bootstrap_95"), row.get("p_holm"), row.get("reading"))


if __name__ == "__main__":
    main()
