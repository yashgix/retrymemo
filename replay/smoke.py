"""Exactly one live DeepSeek request through the real runner path, then stop.

Before sending: the payload must pass the flag scan and the leak check, and its worst-case cost must fit the reserve.
The request has no retries. After it: usage must be complete, the three cost records must agree, the reply's tool
call runs inside the task container (no network), the balance is read before and after, and the source repos must be
unchanged. Any failed check stops with a non-zero exit, and everything is written to <out>/smoke.json.

Run it under the write guard:
  sandbox-exec -p "$(python3 -m replay.guard)" ../v5-env/bin/python -m replay.smoke --out ~/replay-work/smoke
"""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from minisweagent.exceptions import FormatError, InterruptAgentFlow  # imports no model code
from minisweagent.models import GLOBAL_MODEL_STATS

from agent.pricing import HIT, MISS, OUTPUT, cost
from replay import MAX_OUTPUT
from replay.guard import changes, snapshot, watched
from replay.ledger import DEFAULT as LEDGER
from replay.ledger import Ledger
from replay.memory import payload_problems

# replay.run sets mini-swe-agent's environment before importing it, so its names come from there.
from replay.run import (
    CONFIG,
    INSTANCE,
    DefaultAgent,
    balance,
    environment,
    make_model,
    prompt,
)
from replay.tasks import load
from replay.workspace import image_name, sandbox

RESERVE = 0.02
USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")


def worst_case(payload: str) -> float:
    """Every character a token (an overestimate), none cached, the full output allowance."""
    return len(payload) * MISS + MAX_OUTPUT * OUTPUT


def record(agent, observations: list[dict]) -> dict:
    """What the one call returned: usage, the three cost records, the tool calls and what they printed."""
    # The billed reply carries the API response: an assistant message, or a "user" format-error message.
    reply = next((m for m in agent.messages if m.get("extra", {}).get("response")), {})
    response = reply.get("extra", {}).get("response") or {}
    usage = response.get("usage") or {}
    return {
        "usage": {k: usage.get(k) for k in USAGE_FIELDS},
        "cost_agent": agent.cost,
        "cost_global": GLOBAL_MODEL_STATS.cost,
        "calls": GLOBAL_MODEL_STATS.n_calls,
        "finish_reason": (response.get("choices") or [{}])[0].get("finish_reason"),
        "actions": [a.get("command", "")[:300] for a in reply.get("extra", {}).get("actions", [])],
        "observations": [str(o.get("content", ""))[:300] for o in observations],
        "reply_text": (reply.get("content") or "")[:1000],
    }


def stop(report: dict, out: Path, reason: str) -> None:
    report["stopped"] = reason
    (out / "smoke.json").write_text(json.dumps(report, indent=1) + "\n")
    raise SystemExit(f"STOP: {reason}")


def send(agent) -> tuple[list[dict], str | None]:
    """The one request and its tool calls. A reply the agent loop interrupts (no tool call, or an immediate submit)
    was still billed, so its messages and cost are kept."""
    try:
        return agent.step(), None
    except InterruptAgentFlow as e:
        agent.add_messages(*e.messages)
        if isinstance(e, FormatError):
            agent.cost += e.messages[0].get("extra", {}).get("cost", 0.0)
        return [], type(e).__name__
    except Exception as e:
        return [], f"error: {type(e).__name__}: {str(e)[:300]}"


def receipt(key: str, before: float, report: dict, ledger) -> None:
    """DeepSeek's balance after the call. It updates with a delay, so read it up to five times, a minute apart."""
    for _ in range(5):
        time.sleep(60)
        try:
            report["balance_after"] = balance(key)
            ledger.observe(report["balance_after"])
        except Exception as e:
            report["balance_error"] = f"{type(e).__name__}: {str(e)[:200]}"
            return
        if report["balance_after"] < before:
            break
    report["balance_drop"] = round(before - report["balance_after"], 6)


def problems(report: dict) -> list[str]:
    """Every stop condition after the call, all of them checked."""
    usage, found = report["usage"], []
    if str(report.get("interrupted", "")).startswith("error"):
        found.append(f"the request failed: {report['interrupted']}")
    if report.get("interrupted") == "FormatError":
        found.append("the reply had no valid tool call")
    if any(usage[k] is None for k in USAGE_FIELDS):
        found.append("usage data missing")
    else:
        report["cost_recomputed"] = cost(SimpleNamespace(**usage))
        same = [report["cost_agent"], report["cost_global"]]
        if report["calls"] != 1 or any(abs(c - report["cost_recomputed"]) > 1e-9 for c in same):
            found.append("the run's cost records disagree with the usage")
        if usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"] != usage["prompt_tokens"]:
            found.append("cached and uncached tokens do not add up")
    if report.get("balance_error"):
        found.append(f"balance read failed: {report['balance_error']}")
    elif report.get("balance_drop", 0) > max(RESERVE, report.get("cost_recomputed", 0)) + 0.01:  # cent steps
        found.append(f"the balance dropped ${report['balance_drop']}, more than the call can cost")
    if not report["source_repos_unchanged"]:
        found.append("source repos changed")
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="lock")
    parser.add_argument("--arm", default="oracle")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true", help="check the payload, send nothing")
    args = parser.parse_args()
    os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "1"  # no automatic retries
    args.out.mkdir(parents=True, exist_ok=True)
    task = load(args.task)
    text, database = prompt(task, args.arm, args.seed)
    report = {
        "task": args.task,
        "arm": args.arm,
        "seed": args.seed,
        "reserve": RESERVE,
        "prices_per_token": {"miss": MISS, "hit": HIT, "output": OUTPUT},
    }
    roots = watched([task["source_path"]])
    before = snapshot(roots)
    # The model wrapper reads the key when it is built. In check-only mode it gets a value that is never sent.
    key = "check-only" if args.check_only else os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise SystemExit("the smoke call needs DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = key
    with sandbox(task, image_name(task)) as (run_args, env):
        docker_env = environment(task, image_name(task), run_args, env)
        try:
            limits = {"instance_template": INSTANCE, "step_limit": 1, "cost_limit": RESERVE}
            ledger = Ledger(LEDGER, args.out)  # the cumulative cap; the call's own reserve is checked below
            agent = DefaultAgent(make_model("deepseek-flash", task, ledger), docker_env, **CONFIG["agent"] | limits)
            agent.extra_template_vars |= {"task": text, "database": database}
            agent.messages = []
            system = agent._render_template(agent.config.system_template)
            user = agent._render_template(agent.config.instance_template)
            payload = system + "\n" + user
            report |= {
                "payload_chars": len(payload),
                "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "worst_case_cost": round(worst_case(payload), 6),
            }
            if found := payload_problems(task, payload):
                stop(report, args.out, f"payload check failed: {found[:5]}")
            if worst_case(payload) > RESERVE:
                stop(report, args.out, "the worst-case cost of the request is above the reserve")
            if args.check_only:
                stop(report, args.out, "check only: payload clean, nothing sent")
            agent.add_messages(
                agent.model.format_message(role="system", content=system),
                agent.model.format_message(role="user", content=user),
            )
            try:
                report["balance_before"] = balance(key)
                ledger.observe(report["balance_before"])
            except Exception as e:
                stop(report, args.out, f"balance read failed before the call, nothing sent: {type(e).__name__}")
            GLOBAL_MODEL_STATS.cost_limit = RESERVE
            observations, report["interrupted"] = send(agent)
            report |= record(agent, observations)
        finally:
            docker_env.cleanup()
    report["source_repos_unchanged"] = not changes(before, snapshot(roots))
    (args.out / "smoke.json").write_text(json.dumps(report, indent=1) + "\n")  # saved before the wait
    receipt(key, report["balance_before"], report, ledger)
    if found := problems(report):
        stop(report, args.out, "; ".join(found))
    report["stopped"] = None
    (args.out / "smoke.json").write_text(json.dumps(report, indent=1) + "\n")
    fields = ("usage", "cost_recomputed", "balance_drop", "calls", "actions", "interrupted", "source_repos_unchanged")
    print(json.dumps({k: report.get(k) for k in fields}))


if __name__ == "__main__":
    main()
