"""v5: pick first retries of one split that have a SWE-rebench image, in a fixed hash order, and build each arm's text.

Writes one JSON with, per task, the image, the tests, the gold and test patches (for the check, never shown to the
agent), whether the 2024 retry never opened a needed file (the pre-registered subgroup) and the arms' handoff text.
"""

import argparse
import ast
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.handoff_text import ARMS, TOP_FILES, arm_text  # noqa: E402
from recallbench.handoff import handoff  # noqa: E402
from recallbench.retrieve import Corpus  # noqa: E402
from recallbench.unseen import History  # noqa: E402
from tools.handoff import first_needed_step, first_retries, fix_ranking  # noqa: E402
from tools.unseen import extra_tasks  # noqa: E402

EDITED_WEIGHT = 2  # v4 as frozen
ROWS_API = "https://datasets-server.huggingface.co/filter?dataset=nebius/SWE-rebench&config=default&split={}&where={}"
HUB_API = "https://hub.docker.com/v2/repositories/{}/tags/latest"


def get_json(url: str, tries: int = 6) -> dict | None:
    """GET a JSON body; None on 404. The rows API fails or stalls and Docker Hub rate-limits, so those are retried."""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if (e.code < 500 and e.code != 429) or attempt == tries - 1:
                raise
        except TimeoutError, urllib.error.URLError:
            if attempt == tries - 1:
                raise
        time.sleep(5 * 2**attempt)


def rebench_row(instance_id: str) -> dict | None:
    where = urllib.parse.quote(f"\"instance_id\"='{instance_id}'")
    for split in ("test", "filtered"):
        rows = (get_json(ROWS_API.format(split, where)) or {}).get("rows", [])
        if rows:
            return rows[0]["row"]
    return None


def image_name(instance_id: str) -> str:
    """SWE-rebench's image naming, so Docker Hub (fast) is asked before the rows API (slow)."""
    return "swerebench/sweb.eval.x86_64." + instance_id.replace("__", "_1776_").lower()


def image_bytes(image: str) -> int | None:
    tag = get_json(HUB_API.format(image))
    return tag["full_size"] if tag else None


def tests(field: str | list | None) -> list[str]:
    """The rows API gives test lists either as lists or as Python list literals."""
    if isinstance(field, list):
        return field
    return ast.literal_eval(field) if field else []


def pilot_order(instance_id: str) -> str:
    # The published pilot used a private prefix, so its order is reproduced only with PILOT_ORDER_PREFIX set to it.
    prefix = os.environ.get("PILOT_ORDER_PREFIX", "v5-pilot:")
    return hashlib.sha256(f"{prefix}{instance_id}".encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="folder with the pinned SWE-bench-extra parquet")
    parser.add_argument("--attempts", type=Path, required=True, help="tools/attempts.py output")
    parser.add_argument("--split", choices=("train", "dev"), default="train", help="never test")
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--max-gb", type=float, default=2.0, help="largest compressed image to keep")
    parser.add_argument("--rebench", type=Path, help="agent/rebench_rows.py output, used instead of the rows API")
    parser.add_argument("--exclude", type=Path, help="a task file whose tasks are left out")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    tasks = sorted(extra_tasks(args.source), key=lambda t: (t.repo, t.created_at, t.instance_id))
    corpus = Corpus(tasks, [])
    history = History(corpus)
    index = {t.instance_id: q for q, t in enumerate(tasks)}
    local = {}
    if args.rebench:
        with gzip.open(args.rebench, "rt", encoding="utf-8") as f:
            local = {row["instance_id"]: row for row in map(json.loads, f)}
    excluded = {t["task"] for t in json.loads(args.exclude.read_text())["tasks"]} if args.exclude else set()
    pairs = [(a, b) for a, b in first_retries(args.attempts, args.split) if a["instance_id"] in index]
    picked, skipped = [], []
    for failed, retry in sorted(pairs, key=lambda p: pilot_order(p[0]["instance_id"])):
        task_id = failed["instance_id"]
        if task_id in excluded:
            continue
        size = image_bytes(image_name(task_id))
        row = None
        if size and size <= args.max_gb * 1e9:
            row = local.get(task_id) if args.rebench else rebench_row(task_id)
        time.sleep(0.5)
        if not row or row["docker_image"] != image_name(task_id) or not tests(row["FAIL_TO_PASS"]):
            skipped.append({"task": task_id, "row": bool(row), "image_bytes": size})
            continue
        q = index[task_id]
        files = handoff([failed], fix_ranking(corpus, history, q), EDITED_WEIGHT)
        picked.append(
            {
                "task": task_id,
                "split": args.split,
                "repo": row["repo"],
                "image": row["docker_image"],
                "image_bytes": size,
                "problem_statement": row["problem_statement"],
                "fail_to_pass": tests(row["FAIL_TO_PASS"]),
                "pass_to_pass": tests(row["PASS_TO_PASS"]),
                "gold_patch": row["patch"],
                "test_patch": row["test_patch"],
                "gold_files": list(tasks[q].gold_files),
                "handoff_files": files[:TOP_FILES],
                "stalled": first_needed_step(retry, set(tasks[q].gold_files)) is None,
                "arms": {arm: arm_text(arm, files, failed) for arm in ARMS},
            }
        )
        print(f"picked {task_id} ({size / 1e9:.2f} GB)", flush=True)
        if len(picked) == args.count:
            break
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"tasks": picked, "skipped": skipped}, indent=1) + "\n")


if __name__ == "__main__":
    main()
