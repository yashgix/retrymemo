import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.container import HISTORY_CHECK, SCRUB_HISTORY, parse_outcomes, passed
from agent.pricing import cost


class OutcomeParsingTest(unittest.TestCase):
    LOG = (
        "PASSED tests/a.py::test_one\n"
        "PASSED tests/a.py::test_p[a b - c]\n"
        "FAILED tests/a.py::test_two - AssertionError: x\n"
        "XFAIL tests/a.py::test_three - reason\n"
        "ERROR tests/b.py::test_four - ImportError\n"
    )

    def test_each_line_gives_its_id_and_outcome(self):
        found = parse_outcomes(self.LOG)
        self.assertEqual(found["tests/a.py::test_two"], "FAILED")
        self.assertEqual(found["tests/a.py::test_three"], "XFAIL")
        self.assertEqual(found["tests/b.py::test_four"], "ERROR")
        self.assertEqual(found["tests/a.py::test_p[a b - c]"], "PASSED")

    def test_passed_keeps_only_passing_ids(self):
        ids = ["tests/a.py::test_one", "tests/a.py::test_two", "tests/a.py::test_p[a b - c]", "tests/c.py::missing"]
        self.assertEqual(passed(parse_outcomes(self.LOG), ids), [ids[0], ids[2]])


class HistoryScrubTest(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args]
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()

    def test_later_commits_are_gone_and_earlier_ones_stay(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.git(repo, "init", "-q")
            for message in ("old", "base", "fix"):
                (repo / "f.txt").write_text(message)
                self.git(repo, "add", "f.txt")
                self.git(repo, "commit", "-qm", message)
            fix = self.git(repo, "rev-parse", "HEAD")
            self.git(repo, "tag", "v-fix")
            self.git(repo, "checkout", "-q", "HEAD~1")
            scrub = subprocess.run(["bash", "-c", f"{SCRUB_HISTORY} && {HISTORY_CHECK}"], cwd=repo, capture_output=True)
            self.assertEqual(scrub.returncode, 0, scrub.stderr)
            self.assertEqual(self.git(repo, "log", "--all", "--format=%s").split(), ["base", "old"])
            gone = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", fix], capture_output=True)
            self.assertNotEqual(gone.returncode, 0)


class CostTest(unittest.TestCase):
    def test_peak_rates_with_cache_hits(self):
        usage = SimpleNamespace(
            prompt_tokens=1_000_000,
            prompt_cache_hit_tokens=600_000,
            prompt_cache_miss_tokens=400_000,
            completion_tokens=100_000,
        )
        self.assertAlmostEqual(cost(usage), 0.4 * 0.30 + 0.6 * 0.006 + 0.1 * 1.20)

    def test_missing_cache_fields_count_all_input_as_misses(self):
        self.assertAlmostEqual(cost(SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=0)), 0.30)


if __name__ == "__main__":
    unittest.main()
