"""SWE-rebench rows from its parquet export, for when the rows API is down. Run with the agent venv (it has pyarrow).

Keeps the columns prepare_pilot.py uses, one JSON line per task. The test split comes first, like the rows API lookup,
so a task in both splits keeps its test row.
"""

import argparse
import gzip
import json
from pathlib import Path

import pyarrow.parquet as pq

COLUMNS = [
    "instance_id",
    "repo",
    "docker_image",
    "problem_statement",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "patch",
    "test_patch",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("parquet", type=Path, nargs="+", help="test split files first, then filtered")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    seen = set()
    with gzip.open(args.out, "wt", encoding="utf-8") as out:
        for path in args.parquet:
            for row in pq.read_table(path, columns=COLUMNS).to_pylist():
                if row["instance_id"] not in seen:
                    seen.add(row["instance_id"])
                    out.write(json.dumps(row) + "\n")
    print(f"{len(seen)} rows")


if __name__ == "__main__":
    main()
