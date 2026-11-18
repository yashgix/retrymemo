import json
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from recallbench.data import Session
from recallbench.vote import VOTE, Fix, VoteSettings, vote_files, votes

ROOT = Path(__file__).resolve().parent.parent
PLAIN = VoteSettings(depth=10, cap=None, edited_weight=1.0, prior_weight=0.5, fuse_k=0)


def session(opened=(), edited=()):
    return Session("t", 0, "m", True, "submitted", tuple(opened), tuple(edited), ())


class VotesTest(unittest.TestCase):
    def test_each_session_adds_one_over_k_plus_its_rank_to_each_file(self):
        got = votes([session(edited=("a.py",)), session(opened=("a.py", "b.py"))], PLAIN)
        self.assertEqual(got, {"a.py": 1 + 1 / 2, "b.py": 1 / 2})

    def test_cap_spreads_a_long_list_and_edited_files_weigh_more(self):
        settings = replace(PLAIN, cap=1, edited_weight=3.0)
        got = votes([session(opened=("b.py",), edited=("a.py",))], settings)
        self.assertEqual(got, {"a.py": 1.5, "b.py": 0.5})

    def test_only_the_first_depth_sessions_vote_and_empty_ones_are_skipped(self):
        ranked = [session(), session(edited=("a.py",)), session(edited=("b.py",))]
        self.assertEqual(votes(ranked, replace(PLAIN, depth=2)), {"a.py": 1 / 2})


class FixTest(unittest.TestCase):
    def test_a_past_fix_votes_as_a_session_that_edited_its_gold_files(self):
        settings = replace(PLAIN, edited_weight=2.0)
        self.assertEqual(votes([Fix(("a.py",))], settings), votes([session(edited=("a.py",))], settings))


class VoteFilesTest(unittest.TestCase):
    def test_weight_zero_is_the_vote_order_and_weight_one_starts_with_the_prior(self):
        ranked = [session(edited=("a.py",)), session(edited=("b.py",))]
        prior = ["c.py", "b.py"]
        self.assertEqual(vote_files(ranked, prior, replace(PLAIN, prior_weight=0.0, fuse_k=60))[:2], ["a.py", "b.py"])
        self.assertEqual(vote_files(ranked, prior, replace(PLAIN, prior_weight=1.0, fuse_k=60))[:2], prior)

    def test_fusion_rewards_files_both_lists_agree_on(self):
        ranked = [session(edited=("a.py",)), session(edited=("b.py",))]
        self.assertEqual(vote_files(ranked, ["c.py", "b.py"], replace(PLAIN, fuse_k=60))[0], "b.py")

    def test_frozen_settings_are_the_development_choice(self):
        chosen = json.loads((ROOT / "results" / "tuning.json").read_text())["chosen"]
        self.assertEqual(asdict(VOTE), {key: chosen[key] for key in asdict(VOTE)})
