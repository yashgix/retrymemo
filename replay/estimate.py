"""Cost estimate for the paid run, from the 603 measured v5 runs (same agent, model and prices) plus the memory each
arm adds, at three sizes of task. The ceiling is MAX_CAP, enforced by the runner (see replay/__init__.py).

Usage: python3 -m replay.estimate
"""

import json
import statistics

from agent.pricing import HIT, MISS
from replay import ARMS, MAX_CAP, RESULTS, ROOT, RUN_COST_LIMIT, SEEDS
from replay.tasks import NAMES

V5_RUNS = ROOT.parent.parent / "v5-runs"
SCALES = (1.0, 1.5, 2.0)  # these TypeScript repos may need more reading than v5's Python tasks


def v5_runs() -> list[dict]:
    rows = []
    for path in V5_RUNS.glob("v5-*/results.jsonl"):
        rows += [r for r in map(json.loads, path.read_text().splitlines()) if r.get("arm") in ("A", "B")]
    return rows


def memory_cost(chars: int, steps: float) -> float:
    """The memory is in the first message: one cache miss, then a cache hit on every later call."""
    n = (chars + 3) // 4  # same rule as recallbench.text.tokens
    return n * MISS + n * HIT * max(steps - 1, 0)


def main():
    runs = v5_runs()
    steps = statistics.mean(r["steps"] for r in runs)
    call = statistics.mean(r["cost"] / r["steps"] for r in runs if r["steps"])
    manifest = json.loads((ROOT / "memory" / "manifest.json").read_text())
    extra = statistics.mean(memory_cost(t["chars"], steps) for name in NAMES for t in manifest[name]["texts"].values())
    n = len(NAMES) * len(ARMS) * SEEDS
    expected = {}
    for scale in SCALES:
        # A run stops at the first call after RUN_COST_LIMIT, so it costs that plus about one more call.
        per_run = [min(r["cost"] * scale + extra, RUN_COST_LIMIT + call * scale) for r in runs]
        capped = sum(r["cost"] * scale + extra > RUN_COST_LIMIT for r in runs) / len(runs)
        expected[str(scale)] = {
            "per_run": round(statistics.mean(per_run), 4),
            "total": round(n * statistics.mean(per_run), 2),
            "share_hitting_run_limit": round(capped, 3),
        }
    out = {
        "runs": n,
        "v5_runs": len(runs),
        "v5_mean_cost": round(statistics.mean(r["cost"] for r in runs), 4),
        "v5_mean_steps": round(steps, 1),
        "memory_cost_per_run": round(extra, 5),
        "expected": expected,
        "run_cost_limit": RUN_COST_LIMIT,
        "ceiling": MAX_CAP,
    }
    (RESULTS / "replay-cost.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
