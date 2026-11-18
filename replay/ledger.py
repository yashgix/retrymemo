"""The cumulative spend ledger for every paid call of the experiment, across run folders, processes and freezes.

It is an append-only journal (`ledger.jsonl`): each line names its event and the sha256 of the line before it, so an
edited, reordered or deleted line breaks the chain and the ledger refuses to load. Every change happens under an
exclusive file lock, after re-reading the journal, so two processes cannot both spend the same dollar.

Each request goes out inside `dispatch()`, under that lock: refused after a stop or if its worst case could pass the
limit, then reserved, sent, and settled at the real cost. A request that fails keeps its reservation, since it may
have been billed. Holding the lock through the call means a stop lands either before a request or after it.

Any failed check, privacy block or accounting failure journals a halt event (and writes `ledger.stop`), which every
process checks under the lock before each request, so all spending stops until a person runs
`python3 -m replay.ledger --clear "<reason>"` after checking the cause.

A journal cut short at its end keeps a valid chain, so spend is also checked against an outside record: DeepSeek's
own balance. `observe()` takes a balance reading, and if more has left the account since the anchor reading (taken
before the first paid call) than the journal has settled, it journals the difference as spend.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

from replay import WORK

DEFAULT = WORK / "ledger.jsonl"


class BudgetExceeded(Exception):
    """A request was not sent, because its worst-case cost could pass the limit."""


class LedgerInvalid(Exception):
    """The journal is missing, malformed, its chain is broken, or writing it failed."""


class Halted(Exception):
    """Spending was stopped (a failed check, a privacy block, a person), so nothing further is sent."""


class MissingUsage(Exception):
    """A response came back without complete usage data, so its cost cannot be settled."""


PASS_THROUGH = (BudgetExceeded, Halted)  # a named tuple: the formatter targets 3.14, the agent runs on 3.12


def line_hash(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def read_events(path: Path) -> list[dict]:
    """Every event, after checking that each line points at the one before it."""
    try:
        events, prev = [], "genesis"
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event["prev"] != prev:
                raise ValueError(f"the chain breaks at event {len(events)}")
            events.append(event)
            prev = line_hash(line)
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise LedgerInvalid(f"cannot trust the ledger {path}: {e}") from e
    if not events or events[0]["event"] != "open":
        raise LedgerInvalid(f"the ledger {path} does not start with its opening event")
    return events


def state(events: list[dict]) -> dict:
    """Settled spend and open reservations per folder."""
    settled, reserved = {}, {}
    for e in events[1:]:
        folder = e["folder"]
        if e["event"] in ("charge", "settle", "reconcile"):
            settled[folder] = settled.get(folder, 0.0) + e["usd"]
        if e["event"] == "reserve":
            reserved.setdefault(folder, {})[e["ticket"]] = e["usd"]
        if e["event"] == "settle":
            reserved[folder].pop(e["ticket"])
    return {"settled": settled, "reserved": reserved}


def complete(usage) -> bool:
    """Usage the cost can be computed from: whole, non-negative token counts, with cached and uncached input adding up
    exactly to the input total."""
    counts = [
        getattr(usage, k, None)
        for k in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    ]
    whole = all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts)
    return whole and counts[2] + counts[3] == counts[0]


def halted(events: list[dict]) -> str | None:
    """The reason of the last stop not cleared since, if any."""
    reason = None
    for e in events:
        reason = e["reason"] if e["event"] == "halt" else None if e["event"] == "clear" else reason
    return reason


class Ledger:
    def __init__(self, path: Path, folder: Path, limit: float | None = None):
        self.path, self.folder = path, str(folder.expanduser().resolve())
        self.lock_path, self.stop_path = path.with_suffix(".lock"), path.with_suffix(".stop")
        self.thread_lock, self.local, self.failed = threading.Lock(), threading.local(), False
        self.opening = read_events(path)[0]
        authorized = self.opening["authorized_usd"]
        self.limit = min(limit or authorized, authorized)

    @contextlib.contextmanager
    def locked(self):
        """The journal under an exclusive lock shared by every thread and process. Any accounting failure, taking or
        releasing the lock included, is latched (this process, and the stop file) and raised as the non-retryable
        LedgerInvalid; a failure inside is latched before the lock is released."""
        if getattr(self.local, "held", False):  # this thread already holds it (a stop inside a dispatch)
            yield read_events(self.path)
            return
        with self.thread_lock:
            try:
                handle = self.lock_path.open("a")
                fcntl.flock(handle, fcntl.LOCK_EX)
            except Exception as e:
                raise self.latch(e) from e
            self.local.held = True
            try:
                if self.failed:
                    raise Halted("this process had an accounting failure")
                yield read_events(self.path)
            except PASS_THROUGH:
                raise
            except Exception as e:
                raise self.latch(e) from e
            finally:
                self.local.held = False
                unlock_error = None
                try:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                except Exception as e:
                    unlock_error = e
                with contextlib.suppress(OSError):
                    handle.close()
                if unlock_error:  # latched, so this process sends nothing more
                    raise self.latch(unlock_error) from unlock_error

    def latch(self, error: Exception) -> LedgerInvalid:
        """Stop this process for good and leave the stop file for the others."""
        self.failed = True
        with contextlib.suppress(OSError):
            self.write_stop(f"accounting failure: {type(error).__name__}: {error}")
        return LedgerInvalid(f"accounting failure: {type(error).__name__}: {error}")

    def write_stop(self, reason: str) -> None:
        """The stop file, a second signal beside the journal's halt event (used when the journal itself failed)."""
        with self.stop_path.open("a") as f:
            f.write(reason + "\n")

    def halt(self, reason: str) -> None:
        """Stop every process that spends from this ledger: a halt event in the journal, and the stop file. It takes the
        dispatch lock, so a request either went out before the stop or never goes out. A failure here is raised."""
        with self.locked():
            self.append({"event": "halt", "reason": reason[:500]})
            self.write_stop(reason)

    def clear(self, reason: str) -> None:
        """A person's decision, after checking the cause: spending may resume. Journaled like everything else."""
        with self.locked():
            self.append({"event": "clear", "reason": reason[:500]})
            self.stop_path.unlink(missing_ok=True)

    def stopped(self) -> bool:
        if self.failed:
            return True
        with self.locked() as events:
            return halted(events) is not None or self.stop_path.exists()

    def append(self, event: dict) -> None:
        prev = line_hash(self.path.read_text().splitlines()[-1])
        line = json.dumps(event | {"folder": self.folder, "prev": prev}, sort_keys=True)
        with self.path.open("a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def totals(self, events: list[dict]) -> tuple[float, float]:
        """This folder's spend, and the cumulative spend: everything settled plus every open reservation."""
        s = state(events)
        own = s["settled"].get(self.folder, 0.0) + sum(s["reserved"].get(self.folder, {}).values())
        return own, sum(s["settled"].values()) + sum(v for r in s["reserved"].values() for v in r.values())

    def total(self) -> float:
        with self.locked() as events:
            return self.totals(events)[1]

    def own(self) -> float:
        with self.locked() as events:
            return self.totals(events)[0]

    def remaining(self) -> float:
        return self.limit - self.total()

    def head(self) -> dict:
        """Where the journal stands, recorded on every result row so a later rollback is noticed."""
        lines = self.path.read_text().splitlines()
        return {"events": len(lines), "sha256": line_hash(lines[-1])}

    def contains(self, head: dict) -> bool:
        lines = self.path.read_text().splitlines()
        return len(lines) >= head["events"] and line_hash(lines[head["events"] - 1]) == head["sha256"]

    def observe(self, balance: float) -> None:
        """A DeepSeek balance reading. If more has left the account since the anchor than the journal has settled, the
        difference is journaled as spend, so the total never counts less than DeepSeek does."""
        with self.locked() as events:
            settled = sum(state(events)["settled"].values())
            gap = round(self.opening["anchor_balance_usd"] - balance - settled, 9)
            if gap > 0:
                self.append({"event": "reconcile", "usd": gap, "balance": balance})

    def dispatch(self, worst_case: float, send, price, stop: threading.Event):
        """One request under the lock: refuse after a stop or past the limit, reserve the worst case, send, then settle
        the real cost. A failed call keeps its reservation; a response without complete usage stops all spending."""
        with self.locked() as events:
            if stop.is_set() or halted(events) is not None or self.stop_path.exists():
                raise Halted(f"spending is stopped: {halted(events) or 'stop flag or file'}")
            if self.totals(events)[1] + worst_case > self.limit:
                raise BudgetExceeded(f"${worst_case:.4f} more could pass ${self.limit}")
            ticket = uuid.uuid4().hex
            self.append({"event": "reserve", "ticket": ticket, "usd": worst_case})
            try:
                response, failure = send(), None
            except Exception as e:  # raised below, outside the accounting-failure handling
                response, failure = None, e
            if failure is None and not complete(getattr(response, "usage", None)):
                self.append({"event": "halt", "reason": "a response without complete usage data"})
                self.write_stop("a response without complete usage data")
                failure = MissingUsage("the response has no complete usage data, its reservation is kept")
            if failure is None:
                self.append({"event": "settle", "ticket": ticket, "usd": price(response.usage)})
        if isinstance(failure, MissingUsage):
            stop.set()
        if failure is not None:
            raise failure
        return response


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Clear a stop, after a person has checked its cause.")
    parser.add_argument("--clear", required=True, metavar="REASON")
    Ledger(DEFAULT, Path.cwd()).clear(parser.parse_args().clear)
