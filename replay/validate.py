"""$0 validation of the five tasks: rebuild, build the image, then score known changes (the untouched pre code, the
reference fix, the real first attempt and synthetic task-only or memory-only fixes), each several times.

Run under the write guard: sandbox-exec -p "$(python3 -m replay.guard)" python3 -m replay.validate
"""

import argparse
import json
import time

from replay import RESULTS
from replay.guard import changes, snapshot, watched
from replay.scoring import grade, run_tests
from replay.tasks import NAMES, load, patch, withheld
from replay.workspace import build_image, flagged, git, image_checks, isolation, rebuild


def regression_set(task: dict, runs: dict[str, list[dict]]) -> tuple[list[str], list[str]]:
    """Withheld test ids seen in the reference runs, and the other tests that pass in every pre and reference run."""
    reference = [r["outcomes"] for r in runs["reference"]]
    expected = sorted({t for o in reference for t in o if withheld(task, t)})
    both = [r["outcomes"] for r in runs["pre"]] + reference
    steady = set.intersection(*({t for t, s in o.items() if s == "passed"} for o in both))
    return expected, sorted(t for t in steady if not withheld(task, t))


def validate(task: dict, repeats: int) -> dict:
    repo, redacted = rebuild(task)
    checks = isolation(task, repo, redacted)
    image = build_image(task)
    inside = image_checks(task, image)
    commits = int(git(repo, "rev-list", "--count", "--all"))
    privacy = {k: inside.pop(k) for k in ("probe_blocked", "tree_blocked_lines", "tree_blocked_sample")}
    checks |= inside | {"commits_in_image": inside["commits_in_image"] == commits}
    runs = {}
    for kind in ["pre", *task["patches"]]:
        runs[kind] = []
        for _ in range(repeats):
            start = time.time()
            runs[kind].append(run_tests(task, image, patch(task, kind)) | {"seconds": round(time.time() - start)})
    expected, regression = regression_set(task, runs)
    graded = {
        kind: [grade(task, r["outcomes"], regression, expected) | {"applied": r["applied"]} for r in rs]
        for kind, rs in runs.items()
    }
    return {
        "image": image,
        "base": git(repo, "rev-parse", "HEAD").strip(),
        "isolation": checks,
        "privacy": privacy,
        "redacted": redacted,
        "flagged": flagged(repo),
        "expected": expected,
        "regression": regression,
        "graded": graded,
        "seconds": {k: [r["seconds"] for r in rs] for k, rs in runs.items()},
    }


def summary(name: str, result: dict) -> str:
    lines = [
        f"{name}: isolation {'ok' if all(result['isolation'].values()) else result['isolation']}, "
        f"{len(result['expected'])} withheld tests, {len(result['regression'])} regression tests, "
        f"{result['privacy']['tree_blocked_lines']} blocked lines in the tree"
    ]
    for kind, rows in result["graded"].items():
        cells = [("P" if r["pass"] else "-") + ("A" if r["avoided"] else "-") for r in rows]
        lines.append(
            f"  {kind:14} {' '.join(cells)}  task_failed={len(rows[0]['task_failed'])} "
            f"memory_failed={len(rows[0]['memory_failed'])} regression_failed={rows[0]['regression_failed']}"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default=",".join(NAMES))
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    tasks = [load(n) for n in args.tasks.split(",")]
    roots = watched([t["source_path"] for t in tasks])
    before = snapshot(roots)
    out = RESULTS / "replay-validation.json"
    results = json.loads(out.read_text()) if out.exists() else {}
    for task in tasks:
        results[task["name"]] = validate(task, args.repeats)
        print(summary(task["name"], results[task["name"]]), flush=True)
        out.write_text(json.dumps(results, indent=1) + "\n")
    changed = changes(before, snapshot(roots))
    print("source repos unchanged" if not changed else f"SOURCE CHANGED ({len(changed)}): {changed[:10]}")


if __name__ == "__main__":
    main()
