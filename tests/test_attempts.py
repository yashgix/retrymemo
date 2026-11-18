import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("attempts_under_test", ROOT / "tools" / "attempts.py")
attempts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attempts)

PATCH = (
    "diff --git a/src/pkg/core.py b/src/pkg/core.py\n--- a/src/pkg/core.py\n+++ b/src/pkg/core.py\n"
    "@@ -1 +1 @@\n-a\n+b\n"
    "diff --git a/reproduce.py b/reproduce.py\nnew file mode 100644\n"
)


def row(roles, texts):
    return {
        "instance_id": "o__r-1",
        "model_name": "m",
        "target": False,
        "exit_status": "submitted",
        "generated_patch": PATCH,
        attempts.ROLE: roles,
        attempts.TEXT: texts,
    }


class RecordTest(unittest.TestCase):
    def setUp(self):
        roles = ["system", "user", "ai", "user", "ai", "ai", "user"]
        texts = [
            "sys",
            "issue (Current directory: /repo)",
            "look\n```\nopen src/pkg/core.py\n```",
            "1:a\n(Open file: /repo/src/pkg/core.py)\n(Current directory: /repo)",
            "draft without a reply",
            "run\n```\npython reproduce.py\n```",
            "Traceback\nValueError: bad\n(Open file: /repo/reproduce.py)\n(Current directory: /repo)",
        ]
        self.record = attempts.record(row(roles, texts), 7, "o/r", "dev")

    def test_each_step_pairs_the_last_ai_turn_with_its_reply(self):
        self.assertEqual(self.record["steps"], 2)
        self.assertEqual(self.record["commands"], [["open src/pkg/core.py", ""], ["python reproduce.py", "ValueError"]])

    def test_first_open_is_repo_relative_and_skips_files_the_patch_adds(self):
        self.assertEqual(self.record["first_open"], {"src/pkg/core.py": 1})
        self.assertEqual(self.record["edited"], ["src/pkg/core.py"])

    def test_split_puts_used_repos_in_train(self):
        self.assertEqual(attempts.split_of("x", {"o/r"}, "o/r"), "train")
        self.assertIn(attempts.split_of("x", set(), "o/r"), {"train", "dev", "test"})


if __name__ == "__main__":
    unittest.main()
