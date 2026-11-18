"""v4 (SPEC-v4.md): a retry handoff. When an attempt on an unsolved issue fails, rank files for the next attempt."""

from collections import Counter


def created(attempt: dict) -> set[str]:
    """Files the attempt made itself with SWE-agent's `create` command (scratch scripts such as reproduce.py)."""
    return {cmd.split()[1] for cmd, _ in attempt["commands"] if cmd.startswith("create ") and len(cmd.split()) > 1}


def opened(attempt: dict) -> list[str]:
    """Repository files in the order the attempt first opened them, without its own scratch files."""
    own = created(attempt)
    return [path for path, _ in sorted(attempt["first_open"].items(), key=lambda kv: kv[1]) if path not in own]


def handoff(failed: list[dict], baseline: list[str], edited_weight: float) -> list[str]:
    """Files the failed attempts opened or edited, by weighted count, ties by baseline rank, then the baseline."""
    score: Counter = Counter()
    for attempt in failed:
        for path in opened(attempt):
            score[path] += 1.0
        for path in attempt["edited"]:
            score[path] += edited_weight
    rank = {path: position for position, path in enumerate(baseline)}
    ordered = sorted(score, key=lambda path: (-score[path], rank.get(path, len(rank)), path))
    return list(dict.fromkeys(ordered + baseline))
