"""Replay benchmark: rerun real tasks from agent-built repos with different memory, scored by withheld tests."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS_DIR = ROOT / "tasks"
# The folder holding the source projects (each with repo/ and records/), and the scratch folder for runs.
SOURCES = Path(os.environ.get("REPLAY_SOURCES", Path.home() / "replay-sources")).expanduser()
WORK = Path(os.environ.get("REPLAY_WORK", Path.home() / "replay-work")).expanduser()
RESULTS = ROOT.parent / "results"
NODE_IMAGE = "node:22.23.2-bookworm@sha256:dd5847a04b0deee391fa145f1f4c6d214196668b6bcc7988ebed67249f226844"
POSTGRES_IMAGE = "postgres:18.4"
ARMS = ("none", "instructions", "retrieved", "oracle", "random")
SEEDS = 5
# Paid-run limits. Every request reserves its own worst case in the ledger before it is sent (replay/ledger.py), so the
# cumulative total cannot pass MAX_CAP. A run starts only while at least MAX_CALL is left.
MAX_CAP = 10.0
RUN_COST_LIMIT = 0.08
MAX_OUTPUT = 8192
MAX_CALL = 0.10
PARALLEL = 2
