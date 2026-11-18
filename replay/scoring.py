"""Score a change: apply it in a fresh container, add the withheld tests, run the whole suite, read vitest's JSON."""

import base64
import json

from replay.tasks import is_memory_test, withheld
from replay.workspace import container, sandbox, sh

REPORT = "/tmp/replay-vitest.json"
SUITE_TIMEOUT = 900


def outcomes(report: dict) -> dict[str, str]:
    """Test id ("file > describe test") to vitest status. Only "passed" counts as a pass; a skip is not one."""
    found = {}
    for suite in report.get("testResults", []):
        file = suite["name"].removeprefix("/testbed/")
        for test in suite.get("assertionResults", []):
            found[f"{file} > {test['fullName']}"] = test["status"]
    return found


def run_tests(task: dict, image: str, change: bytes, restore: list[str] = (), on_failure=None) -> dict:
    """The change (a binary git diff against the base), the base version of the regression test files (the agent may
    edit tests), the withheld test files, then the full suite. A failure, raised or returned, is reported to
    `on_failure` before any container or sandbox cleanup, which can be slow."""
    with sandbox(task, image, on_failure) as (run_args, _), container(image, run_args) as cid:
        try:
            result = score_in(task, cid, change, restore)
        except Exception as e:
            if on_failure:
                on_failure(e)
            raise
        if result.get("error") and on_failure:
            on_failure(result["error"][:200])
        return result


def score_in(task: dict, cid: str, change: bytes, restore: list[str]) -> dict:
    if change.strip():
        code, out = sh(cid, "git apply --binary --whitespace=nowarn -", stdin=change)
        if code:
            return {
                "applied": False,
                "error": out.decode(errors="replace")[-2000:] or "git apply failed",
                "outcomes": {},
            }
    steps = [f"git checkout HEAD -- {' '.join(restore)}"] if restore else []
    for rel in task["tests"]:
        body = base64.b64encode((task["dir"] / "tests" / rel).read_bytes()).decode()
        steps.append(f"mkdir -p \"$(dirname '{rel}')\" && echo {body} | base64 -d > '{rel}'")
    for step in steps:
        code, out = sh(cid, step)
        if code:
            raise RuntimeError(f"could not set up the tests: {out.decode(errors='replace')[-500:]}")
    sh(cid, f"npx vitest run --reporter=json --outputFile={REPORT} >/tmp/vitest.log 2>&1", timeout=SUITE_TIMEOUT)
    code, raw = sh(cid, f"cat {REPORT}")
    if code:
        _, log = sh(cid, "tail -c 3000 /tmp/vitest.log")
        return {"applied": True, "error": "no vitest report: " + log.decode(errors="replace"), "outcomes": {}}
    return {"applied": True, "outcomes": outcomes(json.loads(raw))}


def infrastructure_failure(row: dict) -> bool:
    """Missing data, not an outcome: a crash (only the harness, the API or Docker raise one, the agent's own endings
    are Submitted or a limit) or a scoring error."""
    return str(row["exit_status"]).startswith("crash") or bool(row.get("scoring_error"))


def cells_done(rows: list[dict]) -> set[tuple]:
    """Cells with a counted run, plus cells already retried once after an infrastructure failure."""
    counted = {(r["task"], r["arm"], r["seed"]) for r in rows if not infrastructure_failure(r)}
    failures = [(r["task"], r["arm"], r["seed"]) for r in rows if infrastructure_failure(r)]
    return counted | {cell for cell in failures if failures.count(cell) >= 2}


def grade(task: dict, found: dict[str, str], regression: list[str], expected: list[str]) -> dict:
    """pass: every task test and every regression test passed. avoided: pass and every memory test passed.
    `expected` is every withheld test id (from the reference runs), so a missing test counts as a failure."""

    def failing(ids: list[str]) -> list[str]:
        return [t for t in ids if found.get(t) != "passed"]

    memory = [t for t in expected if is_memory_test(task, t)]
    # When the remembered lesson is the task's own requirement (simlink), pass needs the memory tests too.
    task_tests = [t for t in expected if withheld(task, t) and (t not in memory or task.get("memory_is_task"))]
    failed_task, failed_memory, failed_regression = failing(task_tests), failing(memory), failing(regression)
    passed = not failed_task and not failed_regression
    return {
        "pass": passed,
        "avoided": passed and not failed_memory,
        "task_failed": failed_task,
        "memory_failed": failed_memory,
        "regression_failed": len(failed_regression),
    }
