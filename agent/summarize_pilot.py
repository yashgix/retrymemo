"""v5 pilot: per-arm totals from the runner's results.jsonl (solved, cost, steps, tokens, time, failures)."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import median

from agent.handoff_text import ARMS


def crashed(run: dict) -> bool:
    return str(run["exit_status"]).startswith("crash")


def arm_summary(runs: list[dict]) -> dict:
    return {
        "runs": len(runs),
        "resolved": sum(r["resolved"] for r in runs),
        "cost_total": round(sum(r["cost"] for r in runs), 4),
        "cost_median": round(median(r["cost"] for r in runs), 4),
        "steps_median": median(r["steps"] for r in runs),
        "seconds_median": median(r["seconds"] for r in runs),
        "input_tokens_median": median(r["tokens"]["input"] for r in runs),
        "output_tokens_median": median(r["tokens"]["output"] for r in runs),
        "first_needed_step_median": median([r["first_needed_step"] for r in runs if r["first_needed_step"]] or [None]),
        "never_touched_needed_file": sum(r["first_needed_step"] is None for r in runs),
        "misled": sum(r["misled"] for r in runs),
        "patch_not_applied": sum(bool(r["patch"].strip()) and not r["applied"] for r in runs),
        "submission_differs": sum(not r["submission_matches"] for r in runs),
        "exit_status": dict(Counter(r["exit_status"] for r in runs)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.results.read_text().splitlines()]
    validation = [r for r in rows if r["arm"] == "validation"]
    runs = [r for r in rows if r["arm"] in ARMS]
    tasks = sorted({r["task"] for r in runs})
    summary = {
        "tasks_run": len(tasks),
        "tasks_failed_validation": [r["task"] for r in validation if not r["ok"]],
        "spend_total": round(sum(r["cost"] for r in runs), 4),
        "arms": {arm: arm_summary([r for r in runs if r["arm"] == arm]) for arm in ARMS},
        "per_task": {t: {r["arm"]: r["resolved"] for r in runs if r["task"] == t} for t in tasks},
    }
    args.out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
