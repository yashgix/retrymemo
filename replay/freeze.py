"""Hashes of every frozen input (task specs, tests, patches, memory texts, validation, harness and analysis code, the
agent code it reuses, and the Docker images), so the analysis can refuse to run if one changed after the freeze.

Usage: python3 -m replay.freeze (write FROZEN.json, then commit and tag it as TAG), python3 -m replay.freeze --check
"""

import hashlib
import json
import subprocess
import sys

from replay import NODE_IMAGE, POSTGRES_IMAGE, ROOT
from replay.tasks import NAMES, load
from replay.workspace import image_name

REPO = ROOT.parent
FROZEN = ROOT / "FROZEN.json"
EXTRA = ("results/replay-validation.json", "agent/requirements.lock")
# The agent's whole Python environment, file bytes included.
SITE = REPO.parent / "v5-env/lib/python3.12/site-packages"
TAG = "replay-analysis-frozen-14"


def files() -> list[str]:
    inside = (p for p in ROOT.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p != FROZEN)
    tests = REPO.glob("tests/test_replay*.py")
    local = [*REPO.glob("agent/*.py"), *REPO.glob("recallbench/*.py")]  # local code the harness imports
    return sorted({*(str(p.relative_to(REPO)) for p in [*inside, *tests, *local]), *EXTRA})


def images() -> dict[str, str]:
    # The base image by its digest alone: Docker keeps that name, while a pulled "tag@digest" name lasts only minutes.
    names = [image_name(load(n)) for n in NAMES] + ["node@" + NODE_IMAGE.split("@")[1], POSTGRES_IMAGE]
    ids = {}
    for name in names:
        done = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", name], capture_output=True, text=True
        )
        ids[name] = done.stdout.strip() if done.returncode == 0 else None
    return ids


def packages() -> dict[str, str]:
    """A digest per top-level entry of the agent's site-packages, over every file beneath it (compiled caches left
    out), so a changed, added or removed file or package is noticed, `.pth` start-up files included."""
    out = {}
    for top in sorted(SITE.iterdir()):
        digest = hashlib.sha256()
        for path in sorted(top.rglob("*") if top.is_dir() else [top]):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                digest.update(str(path.relative_to(SITE)).encode() + b"\0" + path.read_bytes())
        out[top.name] = digest.hexdigest()
    return out


def current() -> dict:
    return {
        "files": {f: hashlib.sha256((REPO / f).read_bytes()).hexdigest() for f in files()},
        "images": images(),
        "packages": packages(),
    }


def tagged() -> str | None:
    done = subprocess.run(["git", "-C", str(REPO), "show", f"{TAG}:replay/FROZEN.json"], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def check() -> list[str]:
    frozen, now = json.loads(FROZEN.read_text()), current()
    problems = [f"changed or added: {f}" for f, h in now["files"].items() if frozen["files"].get(f) != h]
    problems += [f"missing: {f}" for f in frozen["files"] if f not in now["files"]]
    problems += [f"image changed: {n}" for n, i in frozen["images"].items() if now["images"].get(n) != i]
    problems += [
        f"package changed or removed: {n}" for n, h in frozen["packages"].items() if now["packages"].get(n) != h
    ]
    problems += [f"package added: {n}" for n in now["packages"].keys() - frozen["packages"].keys()]
    problems += [f"image added: {n}" for n in now["images"].keys() - frozen["images"].keys()]
    if tagged() != FROZEN.read_text():
        problems.append(f"FROZEN.json differs from the copy in tag {TAG}")
    return problems


if __name__ == "__main__":
    if "--check" in sys.argv:
        problems = check()
        print("\n".join(problems) or "frozen inputs unchanged")
        sys.exit(1 if problems else 0)
    snapshot = current()
    if None in snapshot["images"].values() or not snapshot["packages"]:
        sys.exit(f"an image or package is missing: {snapshot['images']} {snapshot['packages']}")
    FROZEN.write_text(json.dumps(snapshot, indent=1) + "\n")
    print(f"froze {len(snapshot['files'])} files and {len(snapshot['images'])} images")
