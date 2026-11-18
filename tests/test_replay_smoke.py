import importlib.util
import threading
import unittest
from unittest import mock

from example_terms import RULES

from replay import redact

AGENT = importlib.util.find_spec("minisweagent") is not None
USAGE = {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1000}
COST = 1000 * 0.30e-6 + 50 * 1.20e-6


def report(**changes):
    base = {
        "usage": dict(USAGE),
        "cost_agent": COST,
        "cost_global": COST,
        "calls": 1,
        "interrupted": None,
        "balance_drop": 0.0,
        "source_repos_unchanged": True,
    }
    return base | changes


@unittest.skipUnless(AGENT, "needs mini-swe-agent (run with ../v5-env/bin/python)")
class SmokeChecks(unittest.TestCase):
    def setUp(self):
        from replay import smoke

        self.smoke = smoke

    def test_a_clean_call_passes(self):
        self.assertEqual(self.smoke.problems(report()), [])

    def test_each_stop_condition(self):
        cases = {
            "usage data missing": report(usage=USAGE | {"completion_tokens": None}),
            "the run's cost records disagree with the usage": report(cost_agent=0.0),
            "cached and uncached tokens do not add up": report(usage=USAGE | {"prompt_cache_hit_tokens": 5}),
            "balance read failed: URLError": report(balance_error="URLError"),
            "the balance dropped $0.05, more than the call can cost": report(balance_drop=0.05),
            "source repos changed": report(source_repos_unchanged=False),
            "the reply had no valid tool call": report(interrupted="FormatError"),
            "the request failed: error: RateLimitError": report(interrupted="error: RateLimitError"),
        }
        for expected, case in cases.items():
            self.assertIn(expected, self.smoke.problems(case), expected)

    def test_an_interrupted_reply_keeps_its_billed_cost(self):
        from minisweagent.exceptions import FormatError

        class Agent:
            cost, messages = 0.0, []

            def step(self):
                raise FormatError(
                    {"role": "user", "content": "no tool", "extra": {"cost": 0.001, "response": {"usage": USAGE}}}
                )

            def add_messages(self, *messages):
                self.messages = [*self.messages, *messages]

        agent = Agent()
        self.assertEqual(self.smoke.send(agent), ([], "FormatError"))
        self.assertEqual((agent.cost, len(agent.messages)), (0.001, 1))
        self.assertEqual(self.smoke.record(agent, [])["usage"], USAGE)

    def test_every_problem_is_reported_together(self):
        found = self.smoke.problems(
            report(
                interrupted="error: Timeout", usage=USAGE | {"completion_tokens": None}, source_repos_unchanged=False
            )
        )
        self.assertEqual(len(found), 3)

    def test_a_context_overflow_is_a_scored_limit_and_an_outage_a_crash(self):
        from replay.run import ending

        self.assertTrue(
            ending(ValueError("This model's maximum context length is 131072 tokens")).startswith("ContextLimit")
        )
        self.assertTrue(ending(ConnectionError("connection reset")).startswith("crash:"))

    def test_worst_case_counts_every_character_as_a_token(self):
        self.assertAlmostEqual(self.smoke.worst_case("x" * 1000), 1000 * 0.30e-6 + 8192 * 1.20e-6)


@unittest.skipUnless(AGENT, "needs mini-swe-agent (run with ../v5-env/bin/python)")
class RequestGate(unittest.TestCase):
    """The DeepSeek model itself refuses a request before the SDK sees it, and reserves its cost first."""

    def setUp(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from agent import deepseek_model
        from replay.ledger import Ledger

        terms = mock.patch.object(redact, "RULES", RULES)
        terms.start()
        self.addCleanup(terms.stop)
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-not-real")
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "ledger.jsonl"
        opening = {
            "event": "open",
            "authorized_usd": 10.0,
            "anchor_balance_usd": 22.14,
            "folder": "",
            "prev": "genesis",
        }
        path.write_text(json.dumps(opening, sort_keys=True) + "\n")
        self.ledger = Ledger(path, Path(self.tmp.name) / "run")
        self.dm, self.stop = deepseek_model, threading.Event()
        self.model = deepseek_model.DeepSeekModel(
            ledger=self.ledger, stop=self.stop, model_name="deepseek-flash", model_kwargs={"max_tokens": 8192}
        )
        self.sent, self.reply = (
            [],
            SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_tokens=1000, completion_tokens=10, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1000
                )
            ),
        )

        def create(**request):
            self.sent.append(request)
            return self.reply

        self.model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def tearDown(self):
        self.tmp.cleanup()

    def conversation(self, tool_output: str) -> list[dict]:
        return [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "<task>Fix the lock order.</task>"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command": "git log --decorate"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": tool_output},
        ]

    def test_a_clean_request_is_sent_and_settled(self):
        self.model._query(self.conversation("e8fef24 (HEAD -> main) Snapshot"))
        self.assertEqual(len(self.sent), 1)
        self.assertAlmostEqual(self.ledger.own(), 1000 * 0.30e-6 + 10 * 1.20e-6)

    def test_sensitive_text_anywhere_is_never_sent_or_charged(self):
        for text in (
            "e8fef24 (HEAD -> orbit-base) Snapshot",
            '"target": "northwind-m1-db-1"',
            "see /Users/someone/acme-work/x",
            "author jdoe42",
            "[V, getglobex.ai]",
            "Northwind (Batch W26) pitch notes",
            "mail owner@example.com",
        ):
            with self.assertRaises(self.dm.PrivateTextBlocked, msg=text):
                self.model._query(self.conversation(text))
        with self.assertRaises(self.dm.PrivateTextBlocked):
            self.model._query(self.conversation("ok"), extra_body={"globex": 1})  # a key, from the call
        self.model.config.model_kwargs["extra_body"] = {"note": "globex"}  # a setting
        with self.assertRaises(self.dm.PrivateTextBlocked):
            self.model._query(self.conversation("ok"))
        self.assertEqual((self.sent, self.ledger.own()), ([], 0.0))

    def test_a_request_that_could_pass_the_cap_is_not_sent(self):
        self.ledger.limit = 0.001
        with self.assertRaises(self.dm.BudgetExceeded):
            self.model._query(self.conversation("ok"))
        self.assertEqual(self.sent, [])

    def test_a_failed_call_or_missing_usage_keeps_the_reservation(self):
        def fail(**request):
            raise TimeoutError("timed out")

        self.model.client.chat.completions.create = fail
        with self.assertRaises(TimeoutError):
            self.model._query(self.conversation("ok"))
        kept = self.ledger.own()
        self.assertGreater(kept, 0.0098)  # at least the full output allowance
        self.reply.usage = None
        self.model.client.chat.completions.create = lambda **request: self.reply
        with self.assertRaises(self.dm.MissingUsage):
            self.model._query(self.conversation("ok"))
        self.assertGreater(self.ledger.own(), kept)

    def test_after_a_stop_not_even_a_run_under_way_sends_again(self):
        self.stop.set()
        with self.assertRaises(self.dm.Halted):
            self.model._query(self.conversation("ok"))
        self.stop.clear()
        self.ledger.halt("another process stopped")  # the shared stop file
        with self.assertRaises(self.dm.Halted):
            self.model._query(self.conversation("ok"))
        self.assertEqual(self.sent, [])

    def test_a_privacy_block_latches_the_stop_at_once(self):
        with self.assertRaises(self.dm.PrivateTextBlocked):
            self.model._query(self.conversation("HEAD -> orbit-base"))
        self.assertTrue(self.stop.is_set())
        self.assertTrue(self.ledger.stopped())

    def test_a_paid_model_without_the_ledger_cannot_be_built(self):
        with self.assertRaises(self.dm.LedgerInvalid):
            self.dm.DeepSeekModel(ledger=None, model_name="deepseek-flash")

    def test_gate_failures_are_never_retried(self):
        for error in (
            self.dm.PrivateTextBlocked,
            self.dm.MissingUsage,
            self.dm.BudgetExceeded,
            self.dm.LedgerInvalid,
            self.dm.Halted,
        ):
            self.assertIn(error, self.dm.DeepSeekModel.abort_exceptions)


if __name__ == "__main__":
    unittest.main()
