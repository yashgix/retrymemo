"""Extract one compact record per pinned SWE-agent attempt (SPEC-v4.md), streaming one row group at a time.

Writes gzipped JSON lines: task, split, outcome, steps, the step each repo file was first opened, edited files, the
patch, and per step the command and the error it hit. Reads the parquet files that tools/fetch_data.py pins.
"""

import argparse
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.fetch_data import (  # noqa: E402
    ADDED,
    CWD,
    DATA,
    DIFF,
    OPEN_FILE,
    ROLE,
    TASK_COLUMNS,
    TEXT,
    download,
    read_table,
    redact,
    relative,
    row_groups,
)

COLUMNS = ("instance_id", "model_name", "target", "exit_status", "generated_patch", ROLE, TEXT)
COMMAND = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)
ERROR = re.compile(
    r"^(?:\w+\.)*(\w*(?:Error|Exception)\b)|(command not found)|(No such file or directory)", re.MULTILINE
)


def split_of(instance_id: str, used_repos: set[str], repo: str) -> str:
    if repo in used_repos:
        return "train"
    bucket = int(hashlib.sha256(f"recallbench-v4:{instance_id}".encode()).hexdigest()[:8], 16) % 10
    return "train" if bucket < 6 else "dev" if bucket < 8 else "test"


def command(turn: str) -> str:
    blocks = COMMAND.findall(turn or "")
    return blocks[-1].strip().splitlines()[0][:200] if blocks and blocks[-1].strip() else ""


def error(observation: str) -> str:
    match = ERROR.search(observation or "")
    return next((g for g in match.groups() if g), "") if match else ""


def record(row: dict, row_index: int, repo: str, split: str) -> dict:
    """One attempt. Step k is the k-th agent turn and the observation that answers it."""
    pairs, pending = [], None
    for role, text in zip(row[ROLE], row[TEXT], strict=True):
        if role == "ai":
            pending = text or ""
        elif role == "user" and pending is not None:
            pairs.append((pending, text or ""))
            pending = None
    cwd = next((m.group(1).strip() for _, o in pairs if (m := CWD.search(o))), "")
    patch = row["generated_patch"] or ""
    added = set(ADDED.findall(patch))
    first_open: dict[str, int] = {}
    for step, (_, observation) in enumerate(pairs, 1):
        for path in OPEN_FILE.findall(observation):
            if (rel := relative(path, cwd)) is not None and rel not in added:
                first_open.setdefault(rel, step)
    return {
        "instance_id": row["instance_id"],
        "row": row_index,
        "repo": repo,
        "split": split,
        "model": row["model_name"],
        "resolved": bool(row["target"]),
        "exit_status": row["exit_status"],
        "steps": len(pairs),
        "first_open": first_open,
        "edited": sorted(set(DIFF.findall(patch)) - added),
        "patch": redact(patch),
        "commands": [[redact(command(turn)), error(observation)] for turn, observation in pairs],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="folder with the pinned parquet files")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((DATA / "MANIFEST.json").read_text())
    tasks = read_table(download(manifest["tasks_source"], args.source), TASK_COLUMNS)
    repo_of = {t["instance_id"]: t["repo"] for t in tasks}
    used = set(manifest["repos"])
    shards = download(manifest["sessions_source"], args.source / "SWE-agent-trajectories")
    used |= {line.split("\t")[0] for line in (ROOT / "data" / "v3-set-b-repos.txt").read_text().splitlines()}
    offset, written = 0, 0
    with gzip.open(args.out, "wt", encoding="utf-8") as out:
        for shard in shards:
            for group in row_groups(shard, COLUMNS):
                for i in range(len(group["instance_id"])):
                    row = {name: group[name][i] for name in COLUMNS}
                    repo = repo_of.get(row["instance_id"])
                    if repo is not None:
                        rec = record(row, offset + i, repo, split_of(row["instance_id"], used, repo))
                        out.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                        written += 1
                offset += len(group["instance_id"])
    print(f"wrote {written} attempts to {args.out}")


if __name__ == "__main__":
    main()
