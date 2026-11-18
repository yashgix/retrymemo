"""Freeze every memory text the paid run would send: replay/memory/<task>/<arm>-<seed>.md, plus a manifest with
sizes, the records each text holds, the flag scan and the leak check. Nothing is sent anywhere.

Usage: python3 -m replay.prepare
"""

import json
import re

from replay import ARMS, ROOT, SEEDS
from replay.memory import (
    INSTRUCTIONS,
    arm_text,
    born,
    corpus,
    future_hashes,
    gold_keys,
    leaks,
    ranked,
    solution_lines,
    test_titles,
)
from replay.redact import flags
from replay.tasks import NAMES, load

OUT = ROOT / "memory"
ITEM = re.compile(r"^### (\S+) \(written", re.MULTILINE)


def prepare(task: dict) -> dict:
    if born(INSTRUCTIONS) >= task["cutoff_at"]:
        raise ValueError(f"{task['name']}: the instructions file is newer than the cutoff")
    records = corpus(task)
    checks = (solution_lines(task), test_titles(task), future_hashes(task))
    order = [r.key for r in ranked(records, task["text"])]
    rows = {"task_text_leaks": leaks(task, task["text"], checks), "corpus": len(records), "texts": {}}
    rows["gold_ranks"] = {g: order.index(g) + 1 for g in gold_keys(task)}
    folder = OUT / task["name"]
    folder.mkdir(parents=True, exist_ok=True)
    gold = gold_keys(task)
    for arm in ARMS:
        for seed in range(1, SEEDS + 1):
            text = arm_text(task, arm, seed, records)
            (folder / f"{arm}-{seed}.md").write_text(text)
            items = ITEM.findall(text)
            rows["texts"][f"{arm}-{seed}"] = {
                "chars": len(text),
                "items": items,
                "gold_items": [g for g in gold if g in items],
                "flags": flags(text),
                "leaks": leaks(task, text, checks),
            }
    return rows


def main():
    manifest = {name: prepare(load(name)) for name in NAMES}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    for name, rows in manifest.items():
        texts = rows["texts"]
        leaked = sorted({leak for t in texts.values() for leak in t["leaks"]} | set(rows["task_text_leaks"]))
        sizes = {arm: sorted({texts[f"{arm}-{s}"]["chars"] for s in range(1, SEEDS + 1)}) for arm in ARMS}
        gold = {
            arm: [len(texts[f"{arm}-{s}"]["gold_items"]) for s in range(1, SEEDS + 1)]
            for arm in ("retrieved", "oracle", "random")
        }
        print(f"{name}: corpus {rows['corpus']}, chars {sizes}, gold items {gold}, leaks {leaked or 'none'}")


if __name__ == "__main__":
    main()
