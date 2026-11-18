"""Docker helpers for the v5 runs: pull, run without network, apply a patch, run the task's tests."""

import re
import shlex
import shutil
import subprocess
import uuid

PLATFORM = ["--platform", "linux/amd64"]
RUN_ARGS = ["--rm", "--network", "none", *PLATFORM]
# The images' .bashrc activates conda's base env, but the project is installed in the testbed env.
TESTBED_ENV = {"PATH": "/opt/conda/envs/testbed/bin:/opt/conda/bin:/usr/local/bin:/usr/bin:/bin"}
# pytest -rA summary lines. A PASSED line is the whole test id (which may hold spaces), others add " - reason".
RESULT = re.compile(r"^(PASSED|FAILED|ERROR|XPASS|XFAIL|SKIPPED) (.+?)(?: - .*)?$", re.MULTILINE)
PASSED_LINE = re.compile(r"^PASSED (.+)$", re.MULTILINE)
# The images keep the upstream repo, including the merged fix. Keep only the base commit's own history. The published
# runs used a private branch name, renamed here.
SCRUB_HISTORY = (
    "set -e; git checkout -q -B task-base HEAD; "
    "git for-each-ref --format='%(refname)' | grep -vx refs/heads/task-base | xargs -r -n1 git update-ref -d; "
    "git remote | xargs -r -n1 git remote remove; rm -f .git/ORIG_HEAD .git/FETCH_HEAD; "
    "git reflog expire --expire=now --all; git gc -q --prune=now"
)
# Every stored object must be reachable from the base commit.
HISTORY_CHECK = (
    "test $(git cat-file --batch-all-objects --batch-check | wc -l) -eq $(git rev-list --objects --all | wc -l)"
)


def parse_outcomes(log: str) -> dict[str, str]:
    found = {test_id: outcome for outcome, test_id in RESULT.findall(log)}
    return found | {test_id: "PASSED" for test_id in PASSED_LINE.findall(log)}


def free_bytes() -> int:
    return shutil.disk_usage("/System/Volumes/Data").free


def pull(image: str) -> None:
    subprocess.run(["docker", "pull", "-q", *PLATFORM, image], check=True, capture_output=True, timeout=3600)


def remove(image: str) -> None:
    subprocess.run(["docker", "rmi", "-f", image], capture_output=True, timeout=600)


class Container:
    def __init__(self, image: str):
        name = f"v5-{uuid.uuid4().hex[:8]}"
        cmd = ["docker", "run", "-d", "--name", name, "-w", "/testbed", *RUN_ARGS, image, "sleep", "3h"]
        self.id = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600).stdout.strip()

    def sh(self, command: str, stdin: str | None = None, timeout: int = 1800) -> tuple[int, str]:
        env = [arg for key, value in TESTBED_ENV.items() for arg in ("-e", f"{key}={value}")]
        cmd = ["docker", "exec", "-i", *env, self.id, "bash", "-c", command]
        done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout, errors="replace")
        return done.returncode, done.stdout + done.stderr

    def apply(self, patch: str) -> bool:
        if not patch.strip():
            return True
        code, _ = self.sh("git apply --whitespace=nowarn -", stdin=patch if patch.endswith("\n") else patch + "\n")
        return code == 0

    def test_outcomes(self, tests: list[str]) -> dict[str, str]:
        """Run the test files the ids name with pytest; outcome per test id (missing ids did not run)."""
        files = sorted({t.split("::")[0] for t in tests})
        _, out = self.sh("python -m pytest -rA --tb=no -p no:cacheprovider " + " ".join(files), timeout=3600)
        return parse_outcomes(out)

    def scrub_history(self) -> bool:
        code, _ = self.sh(f"{SCRUB_HISTORY} && {HISTORY_CHECK}", timeout=1200)
        return code == 0

    def close(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.id], capture_output=True, timeout=300)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def passed(outcomes: dict[str, str], tests: list[str]) -> list[str]:
    return [t for t in tests if outcomes.get(t) in ("PASSED", "XFAIL")]


def check(image: str, patch: str, test_patch: str, tests: list[str]) -> dict:
    """Apply a patch and the task's test patch in a fresh container, then run the tests. Outcome per test id."""
    with Container(image) as box:
        if not box.apply(patch):
            return {"applied": False, "outcomes": {}}
        if not box.apply(test_patch):
            for path in re.findall(r"^diff --git a/\S+ b/(\S+)", test_patch, re.MULTILINE):
                box.sh(f"git checkout HEAD -- {shlex.quote(path)} 2>/dev/null || rm -f {shlex.quote(path)}")
            if not box.apply(test_patch):
                return {"applied": False, "outcomes": {}}
        return {"applied": True, "outcomes": box.test_outcomes(tests)}
