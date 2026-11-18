import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from replay.ledger import BudgetExceeded, Halted, Ledger, LedgerInvalid, MissingUsage, read_events


def journal(folder: Path, events: list[dict]) -> Path:
    path, prev, lines = folder / "ledger.jsonl", "genesis", []
    for e in events:
        line = json.dumps(e | {"prev": prev}, sort_keys=True)
        lines.append(line)
        prev = hashlib.sha256(line.encode()).hexdigest()
    path.write_text("\n".join(lines) + "\n")
    return path


OPEN = {"event": "open", "authorized_usd": 10.0, "anchor_balance_usd": 22.14, "folder": ""}
PRIOR = [
    OPEN,
    {"event": "charge", "usd": 0.0015, "folder": "/x/smoke"},
    {"event": "charge", "usd": 0.0471, "folder": "/x/paid-run"},
]
USAGE = SimpleNamespace(
    prompt_tokens=100, completion_tokens=10, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=100
)
WORKER = """
import sys, threading
from pathlib import Path
from types import SimpleNamespace
from replay.ledger import Ledger
usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1)
ledger = Ledger(Path(sys.argv[1]), Path(sys.argv[2]))
for _ in range(50):
    ledger.dispatch(0.001, lambda: SimpleNamespace(usage=usage), lambda u: 0.0005, threading.Event())
"""


def call(ledger, worst=0.001, actual=0.0005, usage=USAGE, stop=None):
    return ledger.dispatch(worst, lambda: SimpleNamespace(usage=usage), lambda u: actual, stop or threading.Event())


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = journal(self.dir, PRIOR)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prior_spend_counts_and_a_new_folder_never_resets_it(self):
        ledger = Ledger(self.path, self.dir / "paid-run-2")
        self.assertAlmostEqual(ledger.total(), 0.0486)
        self.assertAlmostEqual(ledger.remaining(), 10 - 0.0486)
        self.assertEqual(ledger.own(), 0.0)

    def test_a_reservation_becomes_the_real_cost_or_stays_if_the_call_failed(self):
        ledger = Ledger(self.path, self.dir / "run")
        call(ledger, worst=0.05, actual=0.01)

        def timeout():
            raise TimeoutError("timed out")

        with self.assertRaises(TimeoutError):  # the SDK's own error, unchanged and retryable
            ledger.dispatch(0.05, timeout, lambda u: 0.0, threading.Event())
        self.assertAlmostEqual(Ledger(self.path, self.dir / "run").own(), 0.06)
        self.assertFalse(ledger.stop_path.exists())

    def test_a_request_that_could_pass_the_cap_is_refused(self):
        ledger = Ledger(self.path, self.dir / "run", limit=0.1)
        call(ledger, worst=0.05, actual=0.05)
        with self.assertRaises(BudgetExceeded):
            call(ledger, worst=0.01)
        self.assertEqual(Ledger(self.path, self.dir / "run", limit=99).limit, 10.0)

    def test_a_balance_gap_is_journaled_and_later_spend_counts_on_top(self):
        ledger = Ledger(self.path, self.dir / "run")
        ledger.observe(22.14 - 9.90)  # the journal says $0.05, the account says $9.90 is gone
        self.assertAlmostEqual(ledger.total(), 9.90)
        call(ledger, worst=0.06, actual=0.06)
        with self.assertRaises(BudgetExceeded):
            call(ledger, worst=0.06, actual=0.06)  # 9.96 + 0.06 would pass $10
        ledger.observe(22.14 - 9.90)  # the same reading again adds nothing
        self.assertAlmostEqual(ledger.total(), 9.96)

    def test_incomplete_usage_stops_all_spending_and_keeps_the_reservation(self):
        ledger, stop = Ledger(self.path, self.dir / "run"), threading.Event()
        partial = SimpleNamespace(prompt_tokens=100, completion_tokens=None)
        with self.assertRaises(MissingUsage):
            call(ledger, worst=0.02, usage=partial, stop=stop)
        self.assertTrue(stop.is_set() and ledger.stopped())
        self.assertAlmostEqual(Ledger(self.path, self.dir / "run").own(), 0.02)

    def test_a_failed_settlement_is_latched_before_anyone_else_can_send(self):
        ledger, other = Ledger(self.path, self.dir / "a"), Ledger(self.path, self.dir / "b")
        real = Ledger.append

        def settle_fails(self, event):
            if event["event"] == "settle":
                raise OSError("fsync failed")
            return real(self, event)

        with mock.patch.object(Ledger, "append", settle_fails), self.assertRaises(LedgerInvalid):
            call(ledger)
        with self.assertRaises(Halted):
            call(other)  # another process sees the stop file written while the lock was still held
        with self.assertRaises(Halted):
            call(ledger)

    def test_a_lock_that_cannot_be_taken_is_latched_and_not_retryable(self):
        ledger = Ledger(self.path, self.dir / "run")
        with mock.patch("replay.ledger.fcntl.flock", side_effect=OSError("no locks")), self.assertRaises(LedgerInvalid):
            call(ledger)
        self.assertTrue(ledger.stopped())
        self.assertTrue(ledger.stop_path.exists())

    def test_cache_counts_must_add_up_to_the_input_exactly(self):
        ledger = Ledger(self.path, self.dir / "run")
        short = SimpleNamespace(
            prompt_tokens=1000, completion_tokens=5, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0
        )  # would charge no input at all
        with self.assertRaises(MissingUsage):
            call(ledger, usage=short)
        self.assertTrue(ledger.stopped())

    def test_a_stop_is_journaled_and_only_an_explicit_clear_lifts_it(self):
        ledger = Ledger(self.path, self.dir / "run")
        ledger.halt("a privacy block")
        ledger.stop_path.unlink()  # the file alone going away does not resume spending
        with self.assertRaises(Halted):
            call(ledger)
        ledger.clear("checked by a person")
        call(ledger)
        self.assertEqual([e["event"] for e in read_events(self.path)][-4:], ["halt", "clear", "reserve", "settle"])

    def test_a_stop_that_cannot_be_written_is_raised_not_lost(self):
        ledger = Ledger(self.path, self.dir / "run")
        with (
            mock.patch.object(Ledger, "write_stop", side_effect=OSError("read-only")),
            self.assertRaises(LedgerInvalid),
        ):
            ledger.halt("a privacy block")

    def test_a_stop_reaches_every_ledger_on_the_journal_and_stays(self):
        first, other = Ledger(self.path, self.dir / "a"), Ledger(self.path, self.dir / "b")
        first.halt("a privacy block")
        with self.assertRaises(Halted):
            call(other)
        self.assertTrue(Ledger(self.path, self.dir / "c").stopped())

    def test_a_stop_waits_for_a_request_in_flight_and_then_nothing_more_goes_out(self):
        ledger, started, sent = Ledger(self.path, self.dir / "run"), threading.Event(), []

        def slow():
            started.set()
            time.sleep(0.3)
            sent.append(1)
            return SimpleNamespace(usage=USAGE)

        worker = threading.Thread(target=ledger.dispatch, args=(0.001, slow, lambda u: 0.0005, threading.Event()))
        worker.start()
        started.wait()
        ledger.halt("stop while a request is out")  # blocks until that request is done
        self.assertEqual(sent, [1])
        worker.join()
        with self.assertRaises(Halted):
            call(ledger)

    def test_an_accounting_failure_is_not_retryable_and_stops_spending(self):
        ledger = Ledger(self.path, self.dir / "run")
        with mock.patch.object(Ledger, "append", side_effect=OSError("disk full")), self.assertRaises(LedgerInvalid):
            call(ledger)
        self.assertTrue(ledger.stopped())

    def test_an_edited_reordered_or_deleted_line_breaks_the_chain(self):
        ledger = Ledger(self.path, self.dir / "run")
        call(ledger, worst=0.05)
        lines = self.path.read_text().splitlines()[:4]
        for broken in (
            [lines[0], lines[1].replace("0.0015", "0.0001"), *lines[2:]],  # an amount lowered
            [lines[0], lines[2], lines[1], lines[3]],  # reordered
            [lines[0], lines[1], lines[3]],  # a charge deleted
        ):
            self.path.write_text("\n".join(broken) + "\n")
            with self.assertRaises(LedgerInvalid):
                Ledger(self.path, self.dir / "run").total()

    def test_a_row_remembers_where_the_journal_stood(self):
        ledger = Ledger(self.path, self.dir / "run")
        head = ledger.head()
        call(ledger)
        self.assertTrue(ledger.contains(head))
        self.path.write_text("\n".join(self.path.read_text().splitlines()[:2]) + "\n")  # cut back
        self.assertFalse(ledger.contains(head))

    def test_a_missing_or_malformed_journal_is_refused(self):
        with self.assertRaises(LedgerInvalid):
            Ledger(self.dir / "absent.jsonl", self.dir / "run")
        (self.dir / "bad.jsonl").write_text("{}\n")
        with self.assertRaises(LedgerInvalid):
            Ledger(self.dir / "bad.jsonl", self.dir / "run")

    def test_two_threads_and_two_processes_lose_nothing(self):
        ledger = Ledger(self.path, self.dir / "threads")

        def work():
            for _ in range(50):
                call(ledger)

        threads = [threading.Thread(target=work) for _ in range(2)]
        procs = [
            subprocess.Popen([sys.executable, "-c", WORKER, str(self.path), str(self.dir / f"p{i}")]) for i in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for p in procs:
            self.assertEqual(p.wait(), 0)
        reread = Ledger(self.path, self.dir / "threads")
        self.assertAlmostEqual(reread.own(), 0.05)
        self.assertAlmostEqual(reread.total(), 0.0486 + 0.1)


if __name__ == "__main__":
    unittest.main()
