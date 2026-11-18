"""Proof that a step left the source repos alone: every file under them, ignored files, caches and .git included,
is compared by size, mtime and ctime before and after. Run the step under `sandbox-exec` with PROFILE as well."""

import os
from pathlib import Path

from replay import RESULTS, SOURCES, WORK

PROFILE = (
    "(version 1)(allow default)"
    f'(deny file-write* (subpath "{SOURCES}"))'
    f'(allow file-write* (subpath "{RESULTS}") (subpath "{WORK}"))'
)


def watched(sources: list[Path]) -> list[Path]:
    """The task source repos and every project's records folder (the memory corpus)."""
    return sorted({*sources, *(p for p in SOURCES.glob("*/records") if p.is_dir())})


def snapshot(roots: list[Path]) -> dict[str, tuple[int, int, int]]:
    seen = {}
    for root in roots:
        for folder, dirs, files in os.walk(root):
            for name in [*dirs, *files]:
                path = os.path.join(folder, name)
                st = os.lstat(path)
                seen[path] = (st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    return seen


def changes(before: dict, after: dict) -> list[str]:
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


if __name__ == "__main__":
    print(PROFILE)
