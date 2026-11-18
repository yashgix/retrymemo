import contextlib
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from example_terms import RULES

from replay import SOURCES, memory, redact, scoring, workspace
from replay.scoring import grade, outcomes
from replay.tasks import is_memory_test

with_terms = mock.patch.object(redact, "RULES", RULES)

TASK = {
    "name": "t",
    "tests": ["src/a.test.ts"],
    "memory_tests": ["^lesson"],
    "gold_memory": [],
    "text": "fix the lock order",
}
EXPECTED = ["src/a.test.ts > task one", "src/a.test.ts > lesson one"]


def record(key: str, text: str) -> memory.Record:
    return memory.Record(key, datetime(2026, 11, 16, tzinfo=memory.EST), text)


class Scoring(unittest.TestCase):
    def test_reads_vitest_json_by_file_and_full_name(self):
        report = {
            "testResults": [
                {
                    "name": "/testbed/src/a.test.ts",
                    "assertionResults": [
                        {"fullName": "task one", "status": "passed"},
                        {"fullName": "lesson one", "status": "skipped"},
                    ],
                }
            ]
        }
        self.assertEqual(outcomes(report), {EXPECTED[0]: "passed", EXPECTED[1]: "skipped"})

    def test_a_skipped_or_missing_test_is_not_a_pass(self):
        found = {EXPECTED[0]: "passed", EXPECTED[1]: "skipped"}
        result = grade(TASK, found, ["src/b.test.ts > other"], EXPECTED)
        self.assertFalse(result["pass"])
        self.assertEqual(result["regression_failed"], 1)

    def test_avoided_needs_the_task_and_the_memory_tests(self):
        task_only = grade(TASK, {EXPECTED[0]: "passed", EXPECTED[1]: "failed"}, [], EXPECTED)
        both = grade(TASK, {t: "passed" for t in EXPECTED}, [], EXPECTED)
        self.assertEqual((task_only["pass"], task_only["avoided"]), (True, False))
        self.assertEqual((both["pass"], both["avoided"]), (True, True))

    def test_memory_tests_count_for_pass_when_the_lesson_is_the_task(self):
        result = grade(TASK | {"memory_is_task": True}, {EXPECTED[0]: "passed", EXPECTED[1]: "failed"}, [], EXPECTED)
        self.assertFalse(result["pass"])

    def test_memory_tests_match_by_name_inside_the_withheld_files_only(self):
        self.assertTrue(is_memory_test(TASK, "src/a.test.ts > lesson one"))
        self.assertFalse(is_memory_test(TASK, "src/other.test.ts > lesson one"))


@with_terms
class Redact(unittest.TestCase):
    def test_rewrites_home_paths_and_private_folders(self):
        text = f"See [x.ts]({SOURCES}/northwind/repo/src/x.ts:3) and {SOURCES}/orbit/records/a.md. Globex too."
        self.assertEqual(redact.prose(text), "See [x.ts](src/x.ts:3) and crossslot/records/a.md. citecheck too.")

    def test_keeps_common_words_outside_paths(self):
        self.assertEqual(redact.prose("a low orbit satellite"), "a low orbit satellite")

    def test_drops_whole_private_blocks_and_masks_real_emails(self):
        text = "keep this\n\n- Verified: Northwind pitch notes\n  on domains\n\nmail a.b@example.com or r@sink.test"
        self.assertEqual(redact.prose(text), "keep this\n\n\nmail <email> or r@sink.test")

    def test_drops_a_private_url_and_a_private_section(self):
        text = "# Spec\nfits [the domain](https://www.getglobex.ai/)\n\n## 1. Pitch story\nI built\n## 2. API\nok"
        self.assertEqual(redact.prose(text), "# Spec\n\n## 2. API\nok")

    def test_code_keeps_every_line_and_test_data(self):
        code = 'const project = "northwind-m1" // Jane\'s OK\nconst to = "r@sink.test" // package eslint@8.7.1'
        self.assertEqual(redact.names(code), code.replace("northwind", "sendward").replace("Jane", "the owner"))

    def test_refuses_a_secret_but_not_a_bare_key_header(self):
        header = "-----BEGIN " + "PRIVATE KEY-----"
        self.assertEqual(redact.names(header), header)
        with self.assertRaises(ValueError):
            redact.names("key sk-" + "a" * 24)


@with_terms
class Boundary(unittest.TestCase):
    def test_blocks_the_known_sensitive_forms(self):
        for text in (
            "HEAD -> orbit-base",
            "northwind-m1-db-1",
            "northwind/repo",
            "jdoe42",
            "/Users/someone/acme-work/x",
            "getglobex.ai",
            "Pitch notes",
            "## Pitch story",
            "mail Owner@example.com",
        ):
            self.assertTrue(redact.blocked(text), text)

    def test_passes_third_party_look_alikes(self):
        for text in (
            "orbital",
            "Northwind sample database",
            "/Users/foo/app",
            "r@sink.test",
            "eslint@8.7.1",
            "orbits/repo",
            "HEAD -> main",
        ):
            self.assertEqual(redact.blocked(text), [], text)

    def test_the_api_key_is_blocked_wherever_it_appears(self):
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "k" * 32}):
            self.assertEqual(redact.blocked("token " + "k" * 32), ["the API key"])

    def test_request_problems_walks_the_whole_request(self):
        request = [{"role": "tool", "content": [{"type": "text", "text": "HEAD -> orbit-base"}]}]
        self.assertEqual(redact.request_problems(request, {}), ["orbit-base"])

    def test_without_private_terms_only_the_generic_patterns_apply(self):
        with mock.patch.object(redact, "RULES", redact.rules({})):
            self.assertEqual(redact.blocked("jdoe42 northwind-m1"), [])
            self.assertEqual(redact.private("see /Users/x"), ["/Users/"])
            self.assertEqual(redact.prose("mail a@example.com"), "mail <email>")


class Sandbox(unittest.TestCase):
    def test_postgres_readiness_has_a_deadline(self):
        clock = iter(range(0, 1000, 30))
        with (
            mock.patch("replay.workspace.time.monotonic", lambda: next(clock)),
            mock.patch("replay.workspace.time.sleep"),
            mock.patch("replay.workspace.subprocess.run", return_value=mock.Mock(returncode=1)),
            self.assertRaises(RuntimeError),
        ):
            workspace.wait_for_postgres("db")

    def test_a_failed_setup_stops_spending_before_cleanup(self):
        events = []
        task = {"database": "X_URL"}

        def docker(*args, **kwargs):
            if args[0] == "run":
                raise RuntimeError("no postgres")
            return "net"

        with (
            mock.patch.object(workspace, "docker", docker),
            mock.patch("replay.workspace.subprocess.run", lambda *a, **k: events.append("cleanup")),
            self.assertRaises(RuntimeError),
        ):
            with workspace.sandbox(task, "img", on_failure=lambda e: events.append("halt")):
                pass
        self.assertEqual(events[0], "halt")


class ScoringFailures(unittest.TestCase):
    def test_a_scoring_failure_stops_spending_before_the_container_is_removed(self):
        events = []
        task = {"database": None, "tests": [], "dir": Path(".")}
        cases = {"returned": (1, b"patch does not apply"), "raised": RuntimeError("docker went away")}
        for kind, result in cases.items():
            events.clear()

            def sh(cid, command, result=result, **kwargs):
                if isinstance(result, Exception):
                    raise result
                return result

            with (
                mock.patch.object(workspace, "docker", lambda *a, **k: "cid"),
                mock.patch("replay.workspace.subprocess.run", lambda *a, **k: events.append("cleanup")),
                mock.patch("replay.scoring.sh", sh),
                contextlib.suppress(RuntimeError),
            ):
                scoring.run_tests(task, "img", b"diff", on_failure=lambda e: events.append("halt"))
            self.assertEqual(events[:2], ["halt", "cleanup"], kind)


@with_terms
class TargetedRedactions(unittest.TestCase):
    def test_only_the_approved_redactions_touch_data_and_tests(self):
        self.assertEqual(
            workspace.redact_file("results/chaos-naive-seed16/report.json", '"target": "northwind-m1-db-1"'),
            '"target": "sendward-m1-db-1"',
        )
        self.assertEqual(workspace.redact_file("results/other.json", "northwind-m1"), "northwind-m1")
        self.assertEqual(workspace.redact_file("acceptance/tests/m1/hygiene.test.ts", "/jdoe42|x\\/|y/i"), "/x\\/|y/i")

    def test_a_doc_block_the_boundary_would_block_is_dropped(self):
        doc = "## 1. Problem\n- Its site says screening [V,\n  getglobex.ai]. So agents act.\n\n- OFAC uses Soundex."
        self.assertEqual(workspace.redact_doc(doc), "## 1. Problem\n\n- OFAC uses Soundex.")


class Arms(unittest.TestCase):
    RECORDS = [record(f"p/records/r{i}.md", f"lock order note {i}\n" * 40) for i in range(30)]

    def test_fill_keeps_whole_items_then_cuts_one_within_the_budget(self):
        text = memory.fill(self.RECORDS, budget=3000)
        self.assertLessEqual(len(text), 3000)
        self.assertGreater(len(text), 3000 - 100)
        self.assertTrue(text.endswith("[...]\n"))

    def test_oracle_starts_with_gold_and_random_never_holds_it(self):
        task = TASK | {"gold_memory": ["p/records/r7.md"]}
        oracle = memory.arm_text(task, "oracle", 1, self.RECORDS)
        self.assertTrue(oracle.startswith(memory.HEADER + "### p/records/r7.md"))
        for seed in range(1, 6):
            self.assertNotIn("r7.md", memory.arm_text(task, "random", seed, self.RECORDS))

    def test_seeds_change_the_random_pick_only(self):
        a, b = (memory.arm_text(TASK, "random", s, self.RECORDS) for s in (1, 2))
        self.assertNotEqual(a, b)
        self.assertEqual(
            memory.arm_text(TASK, "retrieved", 1, self.RECORDS), memory.arm_text(TASK, "retrieved", 2, self.RECORDS)
        )

    def test_leaks_finds_solution_lines_test_names_and_future_commits(self):
        checks = ({"ORDER BY s.id FOR NO KEY UPDATE OF s"}, {"a bounce and a complaint"}, {"5b1bd1a"})
        text = "ORDER BY s.id FOR NO KEY UPDATE OF s, see 5b1bd1a2, a bounce and a complaint"
        self.assertEqual(len(memory.leaks(TASK, text, checks)), 3)
        self.assertEqual(memory.leaks(TASK, "use FOR NO KEY UPDATE", checks), [])


@with_terms
class Paths(unittest.TestCase):
    def test_project_names_map_folders_to_public_names(self):
        self.assertEqual(redact.project_of(SOURCES / "northwind" / "records" / "x.md"), "sendward")
        self.assertEqual(redact.project_of(SOURCES / "orbit" / "records" / "x.md"), "crossslot")
        self.assertIsInstance(redact.project_of(Path(SOURCES / "unknown" / "x")), str)

    def test_task_paths_map_back_to_the_names_on_disk(self):
        self.assertEqual(redact.source_path("crossslot/repo"), "orbit/repo")
        self.assertEqual(
            redact.source_path("sendward/records/REVIEW-reviewer-1.md"), "northwind/records/REVIEW-acme-1.md"
        )


if __name__ == "__main__":
    unittest.main()
