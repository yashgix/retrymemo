"""Run the agent on task × arm × seed and score each run with the withheld tests.

--model scripted:<patch> replays a known change through the whole harness at $0 (no API, no key read).
--model deepseek-flash is the paid run: it needs --cap (at most $10) and stops starting runs before the cap.

Always run it under the write guard, with no other work going on in the five source repos:
  sandbox-exec -p "$(python3 -m replay.guard)" ../v5-env/bin/python -m replay.run --model scripted:reference \\
    --out ~/replay-work/dry-run
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"  # no cost-map download at import
os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "4"

import yaml  # noqa: E402
from minisweagent.agents.default import DefaultAgent  # noqa: E402
from minisweagent.config import builtin_config_dir  # noqa: E402
from minisweagent.environments.docker import DockerEnvironment  # noqa: E402
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output  # noqa: E402

from replay import ARMS, MAX_CALL, MAX_CAP, MAX_OUTPUT, PARALLEL, RESULTS, ROOT, RUN_COST_LIMIT, SEEDS  # noqa: E402
from replay.freeze import check  # noqa: E402
from replay.guard import changes, snapshot, watched  # noqa: E402
from replay.ledger import DEFAULT as LEDGER  # noqa: E402
from replay.ledger import Ledger  # noqa: E402
from replay.memory import payload_problems  # noqa: E402
from replay.redact import request_problems  # noqa: E402
from replay.scoring import cells_done, grade, run_tests  # noqa: E402
from replay.tasks import NAMES, load, patch  # noqa: E402
from replay.workspace import image_name, sandbox  # noqa: E402

MODEL = "deepseek-flash"
STEP_LIMIT = 60
WALL_LIMIT_SECONDS = 1800
COMMAND_TIMEOUT = 300
BALANCE_URL = "https://api.deepseek.com/user/balance"
STOP = threading.Event()
STOP_REASONS: list[str] = []
ACTIVE_LEDGER: list[Ledger] = []
CONFIG = yaml.safe_load((builtin_config_dir / "benchmarks" / "swebench.yaml").read_text())
SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
INSTANCE = (
    """<task>
{{task}}
</task>

<instructions>
You are a software engineer working in the git repository at /testbed (Node 22, dependencies installed, no network).
Make the change the task asks for, in a way that fits the codebase. You may add or change tests.
Run tests with `npx vitest run <file>`.{{database}}
In each response, give your reasoning briefly and make at least one bash tool call. Each command runs in a new
subshell. Do not use interactive tools.
Your change is read from the working tree, so there is no patch to write. When you are done, run exactly:
"""
    + SUBMIT
    + "\n</instructions>\n"
)


def prompt(task: dict, arm: str, seed: int) -> tuple[str, str]:
    memory = (ROOT / "memory" / task["name"] / f"{arm}-{seed}.md").read_text()
    db = f"\nPostgres for the database tests is reachable through ${task['database']}." if task["database"] else ""
    return task["text"] + ("\n\n" + memory if memory else ""), db


def scripted(kind: str, task: dict):
    """Two steps: apply a known change (none for "pre"), then submit."""
    body = base64.b64encode(patch(task, kind)).decode()
    steps = ([f"echo {body} | base64 -d | git apply --binary"] if body else []) + [SUBMIT]
    outputs = [
        make_toolcall_output(
            None,
            [
                {
                    "id": f"c{i}",
                    "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": cmd})},
                }
            ],
            [{"command": cmd, "tool_call_id": f"c{i}"}],
        )
        for i, cmd in enumerate(steps)
    ]
    for output in outputs:
        output["extra"]["cost"] = 0.0
    return DeterministicToolcallModel(outputs=outputs, cost_per_call=0.0)


def make_model(name: str, task: dict, ledger: Ledger | None = None):
    """The scripted model, or DeepSeek, whose every request passes the privacy and ledger gate."""
    if name.startswith("scripted:"):
        return scripted(name.removeprefix("scripted:"), task)
    from agent.deepseek_model import DeepSeekModel

    kwargs = {"parallel_tool_calls": True, "max_tokens": MAX_OUTPUT}
    return DeepSeekModel(ledger=ledger, stop=STOP, **CONFIG["model"] | {"model_name": MODEL, "model_kwargs": kwargs})


def balance(key: str) -> float:
    """An accounting read, not a model call: DeepSeek's USD balance."""
    request = urllib.request.Request(
        BALANCE_URL, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        infos = json.loads(response.read())["balance_infos"]
    return float(next(i["total_balance"] for i in infos if i["currency"] == "USD"))


def change(container_id: str, base: str) -> bytes:
    """Everything the agent changed, new files included, as a binary diff against the base commit."""
    cmd = [
        "docker",
        "exec",
        container_id,
        "bash",
        "-c",
        f"cd /testbed && git add -A && git diff --cached --binary {base}",
    ]
    return subprocess.run(cmd, capture_output=True, timeout=300, check=True).stdout  # a failure is a crash, not a score


def usage(messages: list[dict]) -> dict:
    totals = {"input": 0, "cached_input": 0, "output": 0, "calls_without_usage": 0}
    for message in (m for m in messages if m.get("role") == "assistant"):
        u = (message.get("extra", {}).get("response") or {}).get("usage") or {}
        totals["calls_without_usage"] += not u
        totals["input"] += u.get("prompt_tokens") or 0
        totals["cached_input"] += u.get("prompt_cache_hit_tokens") or 0
        totals["output"] += u.get("completion_tokens") or 0
    return totals


def environment(task: dict, image: str, run_args: list[str], env: dict) -> DockerEnvironment:
    cfg = {k: v for k, v in CONFIG["environment"].items() if k != "environment_class"}
    shell_env = {k: v for k, v in cfg.pop("env").items() if k != "BASH_ENV"} | env
    return DockerEnvironment(
        **cfg | {"image": image, "run_args": run_args, "env": shell_env, "timeout": COMMAND_TIMEOUT}
    )


def attempt(task: dict, arm: str, seed: int, args, base: str):
    """The agent's run. Returns the agent (None if it never started), its exit status and its change."""
    agent, status, diff = None, "", b""
    text, database = prompt(task, arm, seed)
    if refused := payload_problems(task, text):
        return None, f"crash:payload refused: {refused[:3]}", b""
    name = f"{task['name']}.{arm}.{seed}"
    try:
        with sandbox(task, image_name(task), on_failure=lambda e: halt(f"{name} sandbox failed: {e}")) as (
            run_args,
            env,
        ):
            docker_env = None
            try:
                docker_env = environment(task, image_name(task), run_args, env)
                limits = {
                    "instance_template": INSTANCE,
                    "step_limit": STEP_LIMIT,
                    "cost_limit": RUN_COST_LIMIT,
                    "wall_time_limit_seconds": WALL_LIMIT_SECONDS,
                    "output_path": args.out / f"{name}.traj.json",
                }
                agent = DefaultAgent(make_model(args.model, task, args.ledger), docker_env, **CONFIG["agent"] | limits)
                try:
                    status = agent.run(text, database=database).get("exit_status", "")
                except Exception as e:
                    status = ending(e)
                    if status.startswith("crash"):
                        raise
                diff = change(docker_env.container_id, base)  # also after a context limit: its edits are scored
            except Exception as e:  # anything else here is the harness's failure, never the agent's
                if not status.startswith("crash"):
                    status = "crash:" + ending(e).removeprefix("crash:")
                halt(f"{name} crashed: {status[:200]}")  # stop spending before any cleanup can hang
            finally:
                if docker_env:
                    docker_env.cleanup()
    except Exception as e:  # the sandbox itself failed (already halted by on_failure)
        if not status.startswith("crash"):
            status = f"crash:{type(e).__name__}: {str(e)[:200]}"
    return agent, status, diff


def ending(error: Exception) -> str:
    """A context-length error is the agent's own limit (scored). Anything else is a crash (missing data)."""
    text = f"{type(error).__name__}: {str(error)[:200]}"
    return (
        f"ContextLimit: {text}"
        if re.search(r"context (length|window)|maximum context", str(error), re.I)
        else f"crash:{text}"
    )


def score(task: dict, diff: bytes, valid: dict) -> dict:
    """Never raises: a scoring error is a failed run, so its cost is still logged."""
    try:
        stop = lambda e: halt(f"{task['name']}: scoring failed: {str(e)[:200]}")  # noqa: E731
        scored = run_tests(task, image_name(task), diff, regression_files(valid), on_failure=stop)
        error = scored.get("error")
    except Exception as e:
        scored, error = {"applied": False, "outcomes": {}}, f"{type(e).__name__}: {str(e)[:200]}"
    graded = grade(task, scored["outcomes"], valid["regression"], valid["expected"])
    return {"applied": scored["applied"], "scoring_error": error, **graded}


def halt(reason: str) -> None:
    """Stop this grid, and every process spending from the ledger (its stop file)."""
    STOP_REASONS.append(reason)
    STOP.set()
    if ACTIVE_LEDGER:
        try:
            ACTIVE_LEDGER[0].halt(reason)
        except Exception as e:  # the local stop is set; say loudly that other processes may not know
            print(f"COULD NOT RECORD THE STOP IN THE LEDGER: {type(e).__name__}: {e}", flush=True)


def run_one(task: dict, arm: str, seed: int, args, valid: dict, log) -> None:
    """One run, its score and its checks. Any exception stops the grid; the ledger already holds every call's cost."""
    name = f"{task['name']}.{arm}.{seed}"
    try:
        if STOP.is_set() or (args.paid and args.ledger.remaining() < MAX_CALL):
            return  # after a stop, or with too little budget left for a call, start nothing new
        start = time.time()
        agent, status, diff = attempt(task, arm, seed, args, valid["base"])
        (args.out / f"{name}.diff").write_bytes(diff)
        if status.startswith("crash"):
            halt(f"{name} crashed: {status[:200]}")  # an outage would crash every later run
        row = {
            "task": task["name"],
            "arm": arm,
            "seed": seed,
            "freeze": args.freeze_id,
            "exit_status": status,
            "steps": agent.n_calls if agent else 0,
            "cost": round(agent.cost, 6) if agent else 0.0,
            "tokens": usage(agent.messages) if agent else {},
            "seconds": round(time.time() - start),
            "prompt_chars": len(prompt(task, arm, seed)[0]),
            **score(task, diff, valid),
        }
        if row["scoring_error"]:
            halt(f"{name}: scoring failed: {row['scoring_error'][:200]}")
        checks = after_run(row, args)
        log(row | {"checks": checks, "ledger_head": args.ledger.head() if args.paid else None})
    except Exception as e:
        halt(f"{name}: the runner failed: {type(e).__name__}: {str(e)[:200]}")


def after_run(row: dict, args) -> dict:
    """The secondary checks after every run. Any failure stops the grid."""
    name = f"{row['task']}.{row['arm']}.{row['seed']}"
    trajectory = args.out / f"{name}.traj.json"
    if row["steps"] and not trajectory.exists():
        halt(f"{name}: the trajectory is missing")
    messages = json.loads(trajectory.read_text()).get("messages", []) if trajectory.exists() else []
    checks = {
        "transcript": request_problems(messages),
        "source_changes": len(changes(args.before, snapshot(args.roots))),
    }
    if args.paid:
        now = balance(os.environ["DEEPSEEK_API_KEY"])
        args.ledger.observe(now)  # the ledger never counts less than DeepSeek's own balance drop
        checks["balance_drop"] = round(args.balance_before - now, 2)
        checks["ledger_here"] = round(args.ledger.own() - args.own_start, 6)
    if checks["transcript"]:
        halt(f"{name}: the transcript audit found {checks['transcript'][:5]}")
    if checks["source_changes"]:
        halt(f"{name}: a source repo changed")
    if checks.get("balance_drop", 0) > checks.get("ledger_here", 0) + 0.03:  # the balance has cent steps
        halt(f"{name}: the balance dropped more than this folder spent, so something else is spending")
    no_usage = row["steps"] and not row["tokens"].get("input") or row["tokens"].get("calls_without_usage")
    if args.paid and no_usage:
        halt(f"{name}: usage data missing")
    return checks


def resume_problems(rows: list[dict], args) -> list[str]:
    """Rows already in the folder must come from this freeze, and their ledger position must still be in the journal."""
    found = [
        f"{r['task']}.{r['arm']}.{r['seed']}: made under another freeze"
        for r in rows
        if r.get("freeze") != args.freeze_id
    ]
    if args.paid:
        found += [
            f"{r['task']}.{r['arm']}.{r['seed']}: its ledger position is gone"
            for r in rows
            if r.get("ledger_head") and not args.ledger.contains(r["ledger_head"])
        ]
    return found


def regression_files(valid: dict) -> list[str]:
    return sorted({t.split(" > ", 1)[0] for t in valid["regression"]})


def logger(out: Path, ledger: Ledger | None):
    lock = threading.Lock()

    def log(row: dict) -> None:
        with lock:
            with (out / "results.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")
            if ledger is not None:
                (out / "spend.json").write_text(json.dumps({"total": round(ledger.own(), 6)}))
        print(
            f"{row['task']} {row['arm']} {row['seed']}: pass={row['pass']} avoided={row['avoided']} "
            f"steps={row['steps']} ${row['cost']:.4f} {row['exit_status']}",
            flush=True,
        )

    return log


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help=f"{MODEL} or scripted:<patch kind>")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tasks", default=",".join(NAMES))
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--seeds", type=int, default=SEEDS)
    parser.add_argument("--cap", type=float, default=0.0, help="total spend cap in USD (paid model only)")
    args = parser.parse_args()
    args.paid = not args.model.startswith("scripted:")
    if args.paid and not 0 < args.cap <= MAX_CAP:
        parser.error(f"the paid model needs --cap between 0 and {MAX_CAP}")
    args.out.mkdir(parents=True, exist_ok=True)
    args.freeze_id = hashlib.sha256((ROOT / "FROZEN.json").read_bytes()).hexdigest()
    args.ledger = None
    if args.paid:
        if problems := check():
            parser.error("frozen inputs changed, refusing to spend:\n" + "\n".join(problems))
        args.ledger = Ledger(LEDGER, args.out, limit=args.cap)
        if args.ledger.stopped():
            parser.error(f"spending is stopped until a person checks and removes {args.ledger.stop_path}")
        ACTIVE_LEDGER.append(args.ledger)
        args.own_start = args.ledger.own()
        if not os.environ.get("DEEPSEEK_API_KEY"):
            parser.error("a paid run needs DEEPSEEK_API_KEY")
        print(
            f"ledger total ${args.ledger.total():.6f} of ${args.cap}, every request reserves its worst case first",
            flush=True,
        )
    validation = json.loads((RESULTS / "replay-validation.json").read_text())
    log_path = args.out / "results.jsonl"
    rows = [json.loads(line) for line in log_path.read_text().splitlines()] if log_path.exists() else []
    if problems := resume_problems(rows, args):
        parser.error("this folder cannot be resumed:\n" + "\n".join(problems[:10]))
    done = cells_done(rows)
    plan = [
        (load(t), a, s)
        for s in range(1, args.seeds + 1)
        for t in args.tasks.split(",")
        for a in args.arms.split(",")
        if (t, a, s) not in done
    ]
    args.roots = watched([load(n)["source_path"] for n in NAMES])
    args.before = snapshot(args.roots)
    log = logger(args.out, args.ledger)
    receipts = {"before": balance(os.environ["DEEPSEEK_API_KEY"])} if args.paid else {}
    args.balance_before = receipts.get("before")
    if args.paid:
        args.ledger.observe(args.balance_before)
    with ThreadPoolExecutor(PARALLEL) as pool:
        for future in [pool.submit(run_one, t, a, s, args, validation[t["name"]], log) for t, a, s in plan]:
            future.result()
    if args.paid:
        try:  # DeepSeek updates the balance with a delay, so this receipt can lag behind the spend
            receipts["after"] = balance(os.environ["DEEPSEEK_API_KEY"])
        except Exception as e:
            receipts["after_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        (args.out / f"balance-{int(time.time())}.json").write_text(json.dumps(receipts) + "\n")
    if args.paid:
        print(f"ledger: this folder ${args.ledger.own():.6f}, cumulative ${args.ledger.total():.6f}", flush=True)
    if STOP.is_set():
        print("STOPPED:", "; ".join(STOP_REASONS))
    changed = changes(args.before, snapshot(args.roots))
    print("source repos unchanged" if not changed else f"SOURCE CHANGED ({len(changed)}): {changed[:10]}")


if __name__ == "__main__":
    main()
