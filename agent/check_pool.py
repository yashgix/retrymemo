"""v5 pool check: how much room the tasks leave for a file handoff, from task metadata only. No agent runs.

Dev tasks get metadata only (image, issue, real fix). The 2024 retry and the handoff are looked at on train only, to
see what the metadata says about where a handoff could help.
"""

import argparse
import json
import sys
import time
import urllib.error
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.prepare_pilot import EDITED_WEIGHT, image_bytes, image_name  # noqa: E402
from recallbench.handoff import handoff  # noqa: E402
from recallbench.retrieve import Corpus  # noqa: E402
from recallbench.unseen import History  # noqa: E402
from tools.fetch_data import read_table  # noqa: E402
from tools.handoff import first_needed_step, first_retries, fix_ranking  # noqa: E402
from tools.unseen import extra_tasks  # noqa: E402


def names(path: str) -> set[str]:
    """Ways an issue can name a file: its path, its file name, its dotted module."""
    short = path.removeprefix("src/").removeprefix("lib/")
    module = short.removesuffix(".py").removesuffix("/__init__")
    out = {path, short}
    if not short.endswith("__init__.py"):
        out.add(short.rsplit("/", 1)[-1])
    if "/" in module:
        out.add(module.replace("/", "."))
    return out


def issue_names_fix_file(issue: str, gold: tuple[str, ...]) -> bool:
    return any(name in issue for path in gold for name in names(path))


def changed_lines(patch: str) -> int:
    return sum(
        1
        for line in patch.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---")) and line[1:].strip()
    )


def features(task, patch: str) -> dict:
    return {
        "names_fix_file": issue_names_fix_file(task.issue, task.gold_files),
        "fix_files": len(task.gold_files),
        "fix_lines": changed_lines(patch),
    }


def hub_sizes(ids: list[str], cache: Path) -> dict[str, int | None]:
    """Image sizes from Docker Hub, one at a time (it rate-limits), cached so a rerun asks only for new ids."""
    sizes = json.loads(cache.read_text()) if cache.exists() else {}
    for task_id in ids:
        while task_id not in sizes:
            try:
                sizes[task_id] = image_bytes(image_name(task_id))
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    raise
                time.sleep(60)
            time.sleep(1)
        cache.write_text(json.dumps(sizes))
    return sizes


def share(rows: list[dict], key: str) -> float:
    return round(sum(r[key] for r in rows) / len(rows), 3) if rows else 0.0


def describe(rows: list[dict]) -> dict:
    return {
        "tasks": len(rows),
        "issue_names_fix_file": share(rows, "names_fix_file"),
        "fix_files_median": median(r["fix_files"] for r in rows) if rows else None,
        "multi_file_fix": round(sum(r["fix_files"] > 1 for r in rows) / len(rows), 3) if rows else 0.0,
        "fix_lines_median": median(r["fix_lines"] for r in rows) if rows else None,
    }


def train_by_naming(rows: list[dict]) -> dict:
    """On train: does the 2024 retry find a needed file, and does the handoff point to one, by issue naming?"""
    out = {}
    for label, named in (("issue_names_fix_file", True), ("issue_names_no_fix_file", False)):
        group = [r for r in rows if r["names_fix_file"] is named]
        found = [r["retry_first_needed_step"] for r in group if r["retry_first_needed_step"] is not None]
        out[label] = describe(group) | {
            "retry_2024_never_opened_needed_file": round(1 - len(found) / len(group), 3),
            "retry_2024_first_needed_step_median": median(found) if found else None,
            "handoff_hit1": share(group, "handoff_hit1"),
            "handoff_hit5": share(group, "handoff_hit5"),
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="folder with the pinned SWE-bench-extra parquet")
    parser.add_argument("--attempts", type=Path, required=True, help="tools/attempts.py output")
    parser.add_argument("--pilot", type=Path, required=True, help="the pilot's tasks.json")
    parser.add_argument("--sizes", type=Path, required=True, help="cache of Docker Hub image sizes")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    tasks = sorted(extra_tasks(args.source), key=lambda t: (t.repo, t.created_at, t.instance_id))
    index = {t.instance_id: q for q, t in enumerate(tasks)}
    patches = {
        r["instance_id"]: r["patch"] for r in read_table(args.source.glob("*.parquet"), ("instance_id", "patch"))
    }
    corpus = Corpus(tasks, [])
    history = History(corpus)

    train = []
    for failed, retry in first_retries(args.attempts, "train"):
        if failed["instance_id"] not in index:
            continue
        q = index[failed["instance_id"]]
        gold = set(tasks[q].gold_files)
        files = handoff([failed], fix_ranking(corpus, history, q), EDITED_WEIGHT)
        train.append(
            features(tasks[q], patches[tasks[q].instance_id])
            | {
                "retry_first_needed_step": first_needed_step(retry, gold),
                "handoff_hit1": bool(gold & set(files[:1])),
                "handoff_hit5": bool(gold & set(files[:5])),
            }
        )

    dev_ids = [a["instance_id"] for a, _ in first_retries(args.attempts, "dev") if a["instance_id"] in index]
    sizes = hub_sizes(dev_ids, args.sizes)
    dev = [
        features(tasks[index[i]], patches[i]) | {"task": i, "repo": tasks[index[i]].repo, "image_bytes": sizes[i]}
        for i in dev_ids
    ]
    with_image = [r for r in dev if r["image_bytes"]]
    pilot_ids = [t["task"] for t in json.loads(args.pilot.read_text())["tasks"]]
    pilot = [features(tasks[index[i]], patches[i]) | {"task": i} for i in pilot_ids]

    summary = {
        "train_first_retries": describe(train),
        "train_by_issue_naming": train_by_naming(train),
        "dev_first_retries": describe(dev),
        "dev_with_image": describe(with_image)
        | {
            "image_gb_median": round(median(r["image_bytes"] for r in with_image) / 1e9, 2),
            "image_gb_total": round(sum(r["image_bytes"] for r in with_image) / 1e9, 1),
            "over_2gb": sum(r["image_bytes"] > 2e9 for r in with_image),
            "repos": len({r["repo"] for r in with_image}),
        },
        "dev_without_image": describe([r for r in dev if not r["image_bytes"]]),
        "pilot": describe(pilot),
        "pilot_tasks": pilot,
        "dev_with_image_tasks": with_image,
    }
    args.out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("_tasks")}, indent=1))


if __name__ == "__main__":
    main()
