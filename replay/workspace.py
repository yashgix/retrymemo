"""The agent's starting point for a task: the pre-task tree as one snapshot commit by a neutral author, with the docs
redacted (they hold private notes), inside a Docker image with dependencies installed
fresh from that commit's lockfile. The source repo is only read (clone --no-local)."""

import contextlib
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from replay import NODE_IMAGE, POSTGRES_IMAGE, WORK, redact
from replay.redact import blocked, blocks, flags, names, prose, request_problems

BASE = "main"
CLEANUP_SECONDS = 120
AUTHOR = ["-c", "user.name=builder", "-c", "user.email=builder@example.invalid"]
DOCKERFILE = f"""FROM {NODE_IMAGE}
COPY repo /testbed
WORKDIR /testbed
RUN npm ci --ignore-scripts --no-audit --no-fund && npm cache clean --force && test -z "$(git status --porcelain)"
"""
CODE = {".ts", ".tsx", ".js", ".mjs", ".cjs", ".yml", ".yaml", ".py", ".toml"}
DATA = {"data", "fixtures", "vendor", "results", "models"}


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env)
    return done.stdout


# Three targeted redactions (approved 17 Nov), found by the request-boundary probe in files no task, test or score
# uses: two are text replacements in single files (the private terms' "targeted" list), and the third, a bullet about
# a private website in onnx's docs, is any doc block the boundary would block (see redact_doc).


def redact_doc(text: str) -> str:
    """prose(), then any block that still holds text the request boundary would block."""
    return "\n".join(b for b in blocks(prose(text)) if not blocked(b))


def redact_file(rel: str, text: str) -> str:
    for pattern, old, new in redact.RULES.targeted:
        if pattern.search(rel):
            text = text.replace(old, new)
    if DATA & set(Path(rel).parts) or Path(rel).suffix not in CODE | {".md"}:
        return text
    return redact_doc(text) if rel.endswith(".md") else names(text)


def redact_tree(repo: Path) -> list[str]:
    """Docs lose the blocks about the company or the owner. Code only gets names replaced. Data is left alone, apart
    from the targeted redactions."""
    changed = []
    for rel in git(repo, "ls-files").splitlines():
        if not any(p.search(rel) for p, _, _ in redact.RULES.targeted) and (
            DATA & set(Path(rel).parts) or Path(rel).suffix not in CODE | {".md"}
        ):
            continue
        path = repo / rel
        text = path.read_text()
        if (new := redact_file(rel, text)) != text:
            path.write_text(new)
            changed.append(rel)
    return changed


def commit(repo: Path, message: str, date: str) -> None:
    env = os.environ | {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    git(repo, *AUTHOR, "commit", "-q", "--no-verify", "-m", message, env=env)


def rebuild(task: dict) -> tuple[Path, list[str]]:
    """Clone, take pre's tree as the only commit, redact it, add the task inputs as a second commit."""
    out = WORK / task["name"] / "repo"
    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "-q", "--no-local", "--no-hardlinks", str(task["source_path"]), str(out)], check=True
    )
    git(out, "checkout", "-q", "--detach", task["pre"])
    for ref in git(out, "for-each-ref", "--format=%(refname)").split():  # the clone's own branches, tags, remotes
        git(out, "update-ref", "-d", ref)
    for remote in git(out, "remote").split():
        git(out, "remote", "remove", remote)
    git(out, "checkout", "-q", "--orphan", BASE)
    redacted = redact_tree(out)
    git(out, "add", "-A")
    commit(out, "Snapshot at the start of the task", task["cutoff"])
    if task["inputs"]:
        for rel in task["inputs"]:
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(task["dir"] / "inputs" / rel, out / rel)
        git(out, "add", "--", *task["inputs"])
        commit(out, "Add task inputs", task["cutoff"])
    for name in ("ORIG_HEAD", "FETCH_HEAD", "packed-refs"):
        (out / ".git" / name).unlink(missing_ok=True)
    git(out, "reflog", "expire", "--expire=now", "--all")
    git(out, "gc", "-q", "--prune=now")
    return out, redacted


def blobs(repo: Path, rev: str) -> dict[str, str]:
    rows = (line.split(maxsplit=3) for line in git(repo, "ls-tree", "-r", rev).splitlines())
    return {path: sha for _, _, sha, path in rows}


def flagged(repo: Path) -> list[str]:
    """Files outside data folders that still hold a name, the owner, or other private words."""
    hits = []
    for rel in git(repo, "ls-files").splitlines():
        path = repo / rel
        if DATA & set(Path(rel).parts) or path.suffix not in CODE | {".md"}:
            continue
        found = flags(path.read_text())
        hits += [f"{rel}: {', '.join(found)}"] if found else []
    return hits


def isolation(task: dict, repo: Path, redacted: list[str]) -> dict:
    """Mechanical checks: pre's files, unchanged except the redacted ones, one or two neutral commits, nothing else."""
    src = task["source_path"]
    pre, snapshot = blobs(src, task["pre"]), blobs(repo, f"{BASE}~1" if task["inputs"] else BASE)
    stored = len(git(repo, "cat-file", "--batch-all-objects", "--batch-check").splitlines())
    reachable = len(git(repo, "rev-list", "--objects", "--all").splitlines())
    return {
        "same_files_as_pre": pre.keys() == snapshot.keys(),
        "only_redacted_files_differ": sorted(p for p in pre if pre[p] != snapshot.get(p)) == sorted(redacted),
        "inputs_only_in_second_commit": sorted(git(repo, "diff", "--name-only", "HEAD~1", "HEAD").split())
        == sorted(task["inputs"])
        if task["inputs"]
        else True,
        "neutral_history": set(git(repo, "log", "--format=%an <%ae>").splitlines())
        == {"builder <builder@example.invalid>"}
        and int(git(repo, "rev-list", "--count", "--all")) == 1 + bool(task["inputs"]),
        "all_objects_reachable": stored == reachable,
        "one_ref": git(repo, "for-each-ref", "--format=%(refname)").split() == [f"refs/heads/{BASE}"],
        "no_withheld_test_file": not any((repo / t).exists() and t not in pre for t in task["tests"]),
    }


def image_name(task: dict) -> str:
    return f"replay-{task['name']}:{task['pre'][:12]}"


def build_image(task: dict) -> str:
    """docker build with network for npm ci only. The image holds no file from the source repo's working tree."""
    context = WORK / task["name"]
    (context / "Dockerfile").write_text(DOCKERFILE)
    subprocess.run(["docker", "build", "-q", "-t", image_name(task), str(context)], check=True, capture_output=True)
    return image_name(task)


IMAGE_PROBE = (
    "find / -xdev \\( -name .vitest-cache -o -name .vite -o -name .vite-temp -o -name '.vitest*' \\) -print"
    " 2>/dev/null | head;"
    " ls -d /Users /home/*/Projects 2>/dev/null;"
    " node -e \"fetch('https://registry.npmjs.org').then(() => console.log('INTERNET'), () => {})\";"
    " git -C /testbed rev-list --all | wc -l"
)


# Commands an agent typically runs first. Their output must pass the request boundary unchanged.
PROBE_COMMANDS = (
    "git log --all --decorate --stat -5",
    "git branch -a -v",
    "git status",
    "git show --stat HEAD",
    "git config --list --show-origin",
    "git remote -v",
    "git reflog",
    "cat .git/HEAD .git/config .git/description",
    "ls -la",
    "cat package.json README.md",
    "head -c 30000 SPEC.md",
    "env",
    "hostname; cat /etc/hosts",
    "grep -rn -i 'error' src | head -200",
)


def privacy_checks(cid: str) -> dict:
    """What the probe commands print, and every line of the working copy, through the request boundary."""
    probe = {}
    for command in PROBE_COMMANDS:
        _, out = sh(cid, f"cd /testbed && {command}")
        if found := request_problems(out.decode(errors="replace")):
            probe[command] = found
    lines = []
    if loose := redact.RULES.loose:  # a loose first pass inside the container, then the exact rule on each line
        _, out = sh(cid, f"grep -r -I -n -i -E '{loose}' /testbed 2>/dev/null | cut -c1-400", timeout=1800)
        lines = [line for line in out.decode(errors="replace").splitlines() if request_problems(line)]
    return {"probe_blocked": probe, "tree_blocked_lines": len(lines), "tree_blocked_sample": lines[:20]}


def image_checks(task: dict, image: str) -> dict:
    """Inside a fresh container: no Vitest or Vite cache anywhere, no host folders, no internet, no bind mounts."""
    with sandbox(task, image) as (run_args, _), container(image, run_args) as cid:
        _, out = sh(cid, IMAGE_PROBE)
        lines = out.decode().split()
        mounts = docker("inspect", "-f", "{{json .Mounts}}", cid)
        privacy = privacy_checks(cid)
    return privacy | {
        "boundary_passes_probe_commands": not privacy["probe_blocked"],
        "no_caches_host_paths_or_internet": len(lines) == 1,
        "commits_in_image": int(lines[-1]),
        "no_mounts": mounts == "[]",
    }


def sh(container: str, command: str, timeout: int = 900, stdin: bytes | None = None) -> tuple[int, bytes]:
    done = subprocess.run(
        ["docker", "exec", "-i", container, "bash", "-c", command], input=stdin, capture_output=True, timeout=timeout
    )
    return done.returncode, done.stdout + done.stderr


def docker(*args: str, timeout: int = 300) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


@contextlib.contextmanager
def sandbox(task: dict, image: str, on_failure=None):
    """A task container with no network. Database tasks get an internal network (no outside route) with a fresh
    Postgres on it. Yields (docker run args, env) and removes everything afterwards."""
    tag = uuid.uuid4().hex[:8]
    env, network, db = {}, "none", None
    try:
        if task["database"]:
            network = docker("network", "create", "--internal", f"replay-{tag}")
            db = docker(
                "run",
                "-d",
                "--rm",
                "--network",
                network,
                "--network-alias",
                "db",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                POSTGRES_IMAGE,
            )
            wait_for_postgres(db)
            env[task["database"]] = "postgres://postgres:postgres@db:5432/postgres"  # trust auth: any password
        run_args = ["--rm", "--network", network, *[a for k, v in env.items() for a in ("-e", f"{k}={v}")]]
        yield run_args, env
    except Exception as e:
        if on_failure:  # before any cleanup, which can be slow
            on_failure(e)
        raise
    finally:
        if db:
            subprocess.run(["docker", "rm", "-f", db], capture_output=True, timeout=CLEANUP_SECONDS)
        if network != "none":
            subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=CLEANUP_SECONDS)


def wait_for_postgres(db: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        check = ["docker", "exec", db, "pg_isready", "-U", "postgres", "-h", "127.0.0.1"]
        with contextlib.suppress(subprocess.TimeoutExpired):
            if subprocess.run(check, capture_output=True, timeout=10).returncode == 0:
                return
        time.sleep(0.5)
    raise RuntimeError("postgres did not start within 90 s")


@contextlib.contextmanager
def container(image: str, run_args: list[str]):
    cid = docker("run", "-d", "-w", "/testbed", *run_args, image, "sleep", "3h")
    try:
        yield cid
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=CLEANUP_SECONDS)
