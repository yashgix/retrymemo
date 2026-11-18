"""v5: run mini-swe-agent on each task in the chosen arms (none, v4 files, v4 files plus note), check each patch.

Per task: pull the image, check that the real fix turns FAIL_TO_PASS green and keeps PASS_TO_PASS green, run every
arm × repeat in parallel (each in its own container without network or later git history), check each patch, remove
the image. The API key stays in this process. A task starts only if its worst-case cost still fits under the cap.
"""

import argparse
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"  # no cost-map download at import
os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "4"

import yaml  # noqa: E402
from minisweagent.agents.default import DefaultAgent  # noqa: E402
from minisweagent.config import builtin_config_dir  # noqa: E402
from minisweagent.environments.docker import DockerEnvironment  # noqa: E402
from minisweagent.models import GLOBAL_MODEL_STATS  # noqa: E402

from agent.container import (  # noqa: E402
    HISTORY_CHECK,
    RUN_ARGS,
    SCRUB_HISTORY,
    TESTBED_ENV,
    check,
    free_bytes,
    passed,
    pull,
    remove,
)
from agent.deepseek_model import DeepSeekModel  # noqa: E402
from agent.handoff_text import ARMS  # noqa: E402
from agent.summarize_pilot import crashed  # noqa: E402

MODEL = "deepseek-flash"
STEP_LIMIT = 100
RUN_COST_LIMIT = 0.40
# The limit is checked before each call, so one more call can follow it: reserve room for a large one.
RUN_RESERVE = RUN_COST_LIMIT + 0.10
WALL_LIMIT_SECONDS = 2700
COMMAND_TIMEOUT = 180
SPARE_BYTES = 10_000_000_000
UNPACK_FACTOR = 3  # an image takes about three times its compressed size on disk
DIFF_FILE = re.compile(r"^diff --git a/(\S+) b/", re.MULTILINE)


def config() -> dict:
    return yaml.safe_load((builtin_config_dir / "benchmarks" / "swebench.yaml").read_text())


def validate(task: dict) -> dict:
    """The real fix must turn every FAIL_TO_PASS green. PASS_TO_PASS ids that fail even with it are dropped."""
    tests = task["fail_to_pass"] + task["pass_to_pass"]
    before = check(task["image"], "", task["test_patch"], tests)
    after = check(task["image"], task["gold_patch"], task["test_patch"], tests)
    f2p_after = passed(after["outcomes"], task["fail_to_pass"])
    f2p_before = passed(before["outcomes"], task["fail_to_pass"])
    return {
        "ok": after["applied"] and len(f2p_after) == len(task["fail_to_pass"]) and len(f2p_before) < len(f2p_after),
        "f2p_passed_before": len(f2p_before),
        "f2p_passed_with_fix": len(f2p_after),
        "p2p_kept": passed(after["outcomes"], task["pass_to_pass"]),
    }


def usage(messages: list[dict]) -> dict:
    totals = {"input": 0, "cached_input": 0, "output": 0}
    for message in messages:
        u = (message.get("extra", {}).get("response") or {}).get("usage") or {}
        totals["input"] += u.get("prompt_tokens") or 0
        totals["cached_input"] += u.get("prompt_cache_hit_tokens") or 0
        totals["output"] += u.get("completion_tokens") or 0
    return totals


def first_needed_step(messages: list[dict], gold: list[str]) -> int | None:
    step = 0
    for message in messages:
        if message.get("role") == "assistant":
            step += 1
            commands = " ".join(a.get("command", "") for a in message.get("extra", {}).get("actions", []))
            if any(path in commands for path in gold):
                return step
    return None


def raw_diff(container_id: str, files: list[str]) -> str:
    """The submitted files' diff read as bytes: the submission text went through text mode, which drops CRs."""
    if not files:
        return ""
    cmd = ["docker", "exec", container_id, "git", "-C", "/testbed", "diff", "--no-color", "--", *files]
    return subprocess.run(cmd, capture_output=True, timeout=300).stdout.decode("utf-8", errors="replace")


def environment(task: dict) -> DockerEnvironment:
    cfg = config()["environment"]
    env_cfg = {k: v for k, v in cfg.items() if k != "environment_class"}
    env_cfg["env"] = {k: v for k, v in env_cfg["env"].items() if k != "BASH_ENV"} | TESTBED_ENV
    env = DockerEnvironment(**env_cfg | {"image": task["image"], "run_args": RUN_ARGS, "timeout": COMMAND_TIMEOUT})
    if env.execute({"command": f"{SCRUB_HISTORY} && {HISTORY_CHECK}"}, timeout=1200)["returncode"] != 0:
        env.cleanup()
        raise RuntimeError("git history after the base commit could not be removed")
    return env


def plan(arms: list[str], repeats: int) -> list[tuple[str, int]]:
    return [(arm, repeat) for repeat in range(1, repeats + 1) for arm in arms]


def make_agent(task: dict, arm: str, repeat: int, env: DockerEnvironment, out: Path) -> DefaultAgent:
    cfg = config()
    agent_cfg = cfg["agent"] | {
        "step_limit": STEP_LIMIT,
        "cost_limit": RUN_COST_LIMIT,
        "wall_time_limit_seconds": WALL_LIMIT_SECONDS,
        "output_path": out / f"{task['task']}.{arm}.{repeat}.traj.json",
    }
    model_cfg = cfg["model"] | {"model_name": MODEL, "model_kwargs": {"parallel_tool_calls": True}}
    return DefaultAgent(DeepSeekModel(**model_cfg), env, **agent_cfg)


def run_arm(task: dict, arm: str, repeat: int, out: Path) -> dict:
    """One agent run. Never raises: a crash is a failed run that keeps whatever it already spent."""
    start, agent, result, patch, submission = time.time(), None, {}, "", ""
    try:
        env = environment(task)
        try:
            agent = make_agent(task, arm, repeat, env, out)
            issue = task["problem_statement"] + ("\n\n" + task["arms"][arm] if task["arms"][arm] else "")
            result = agent.run(issue)
            submission = result.get("submission") or ""
            patch = raw_diff(env.container_id, DIFF_FILE.findall(submission))
        finally:
            env.cleanup()
    except Exception as e:  # recorded, not retried
        result = {"exit_status": f"crash:{type(e).__name__}: {str(e)[:200]}"}
    edited = DIFF_FILE.findall(patch)
    top = task["handoff_files"][0] if task["handoff_files"] else None
    return {
        "task": task["task"],
        "arm": arm,
        "repeat": repeat,
        "exit_status": result.get("exit_status"),
        "steps": agent.n_calls if agent else 0,
        "cost": round(agent.cost, 6) if agent else 0.0,
        "tokens": usage(agent.messages) if agent else {},
        "seconds": round(time.time() - start),
        "first_needed_step": first_needed_step(agent.messages, task["gold_files"]) if agent else None,
        "edited": edited,
        "misled": bool(top and top in edited and top not in task["gold_files"]),
        "submission_matches": submission.replace("\r", "").strip() == patch.replace("\r", "").strip(),
        "patch": patch,
    }


def score(task: dict, run: dict, p2p: list[str]) -> dict:
    tests = task["fail_to_pass"] + p2p
    checked = check(task["image"], run["patch"], task["test_patch"], tests) if run["patch"].strip() else None
    outcomes = checked["outcomes"] if checked else {}
    f2p, kept = passed(outcomes, task["fail_to_pass"]), passed(outcomes, p2p)
    resolved = bool(checked and checked["applied"] and len(f2p) == len(task["fail_to_pass"]) and len(kept) == len(p2p))
    return run | {"applied": bool(checked and checked["applied"]), "f2p_passed": len(f2p), "resolved": resolved}


def spent(results: list[dict], out: Path) -> float:
    """Logged run costs plus the cost in any trajectory whose run never reached the log (a crash)."""
    logged = {(r["task"], r["arm"], str(r.get("repeat", 1))) for r in results if r["arm"] in ARMS}
    unlogged = 0.0
    for path in out.glob("*.traj.json"):
        if tuple(path.name.removesuffix(".traj.json").rsplit(".", 2)) not in logged:
            unlogged += json.loads(path.read_text()).get("info", {}).get("model_stats", {}).get("instance_cost", 0.0)
    return sum(r.get("cost", 0.0) for r in results) + unlogged


def log(path: Path, results: list[dict], row: dict) -> None:
    results.append(row)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def run_task(task: dict, runs: list[tuple[str, int]], out: Path, results: list[dict], log_path: Path) -> None:
    start = time.time()
    pull(task["image"])
    try:
        checked = validate(task)
        row = {"task": task["task"], "arm": "validation", "seconds": round(time.time() - start)} | checked
        log(log_path, results, row)
        print(f"{task['task']}: validation {checked['ok']}", flush=True)
        if not checked["ok"]:
            return
        with ThreadPoolExecutor(len(runs)) as pool:
            for future in as_completed([pool.submit(run_arm, task, arm, repeat, out) for arm, repeat in runs]):
                row = score(task, future.result(), checked["p2p_kept"])
                log(log_path, results, row)
                name = f"{row['arm']}{row['repeat']}"
                print(f"  {name}: resolved={row['resolved']} steps={row['steps']} ${row['cost']:.4f}", flush=True)
    finally:
        remove(task["image"])


def stop_reason(task: dict, runs: int, total: float, cap: float) -> str | None:
    if total + runs * RUN_RESERVE > cap:
        return f"${total:.4f} spent, the next task could pass the ${cap} cap"
    if free_bytes() < task["image_bytes"] * UNPACK_FACTOR + SPARE_BYTES:
        return f"{free_bytes() / 1e9:.1f} GB free, not enough for {task['image']}"
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, nargs="+", required=True, help="task files, run in the order given")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, required=True, help="stop after this many validated tasks")
    parser.add_argument("--cap", type=float, required=True, help="total model spend cap in USD")
    parser.add_argument("--arms", default="A,B", help="comma-separated, from " + ",".join(ARMS))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--prior-spend", type=float, default=0.0, help="spend outside --out that counts to the cap")
    args = parser.parse_args()
    arms = args.arms.split(",")
    if not set(arms) <= set(ARMS) or len(set(arms)) != len(arms) or args.repeats < 1:
        parser.error(f"arms must be distinct, from {ARMS}, and repeats at least 1")
    runs = plan(arms, args.repeats)
    if not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("the pilot needs DEEPSEEK_API_KEY")
    log_path = args.out / "results.jsonl"
    results = [json.loads(line) for line in log_path.read_text().splitlines()] if log_path.exists() else []
    seen = {r["task"] for r in results if r["arm"] == "validation"}
    # mini-swe-agent's own stop, a second guard: it counts this process's calls only.
    GLOBAL_MODEL_STATS.cost_limit = args.cap - args.prior_spend - spent(results, args.out)
    for task in [t for path in args.tasks for t in json.loads(path.read_text())["tasks"]]:
        crashed_tasks = {r["task"] for r in results if r["arm"] in ARMS and crashed(r)}
        complete = {r["task"] for r in results if r["arm"] in ARMS} - crashed_tasks
        if len(complete) >= args.count:
            break
        if task["task"] in seen:
            continue
        total = args.prior_spend + spent(results, args.out)
        if reason := stop_reason(task, len(runs), total, args.cap):
            print(f"stop: {reason}", flush=True)
            break
        run_task(task, runs, args.out, results, log_path)
        # An outage would crash every later run in seconds and use up the pool, so a crash stops the run.
        if any(crashed(r) for r in results if r["task"] == task["task"] and r["arm"] in ARMS):
            print(f"stop: a run of {task['task']} crashed", flush=True)
            break
    print(f"total spend ${args.prior_spend + spent(results, args.out):.4f}", flush=True)


if __name__ == "__main__":
    main()
