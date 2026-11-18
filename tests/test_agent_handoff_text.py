import unittest

from agent.handoff_text import PATCH_LINES, TOP_FILES, arm_text, reproduction


def failed(patch="", commands=(), edited=()):
    return {"patch": patch, "commands": [list(c) for c in commands], "edited": list(edited)}


class ArmTextTest(unittest.TestCase):
    def test_arm_a_is_empty(self):
        self.assertEqual(arm_text("A", ["a.py"], failed()), "")

    def test_arm_b_lists_only_the_top_files(self):
        files = [f"f{i}.py" for i in range(TOP_FILES + 3)]
        text = arm_text("B", files, failed(patch="diff --git a/x b/x"))
        self.assertIn(f"{TOP_FILES}. f{TOP_FILES - 1}.py", text)
        self.assertNotIn(f"f{TOP_FILES}.py", text)
        self.assertNotIn("diff --git", text)

    def test_arm_b_without_files_says_so(self):
        text = arm_text("B", [], failed())
        self.assertIn("It points to no file.", text)
        self.assertNotIn("most relevant first", text)

    def test_arm_c_cuts_the_patch_and_says_it_failed(self):
        patch = "\n".join(f"line {i}" for i in range(PATCH_LINES + 10))
        text = arm_text("C", ["a.py"], failed(patch=patch, edited=["a.py"]))
        self.assertIn("This patch did not fix the issue", text)
        self.assertIn(f"line {PATCH_LINES - 1}", text)
        self.assertNotIn(f"line {PATCH_LINES}\n", text)
        self.assertIn("Files it edited: a.py", text)

    def test_arm_c_without_a_patch(self):
        self.assertIn("It left no patch to existing files.", arm_text("C", ["a.py"], failed()))

    def test_arm_c_leaves_out_files_the_attempt_created(self):
        patch = (
            "diff --git a/reproduce.py b/reproduce.py\nnew file mode 100644\n+print(1)\n"
            "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n-x\n+y\n"
        )
        text = arm_text("C", ["src/a.py"], failed(patch=patch, edited=["src/a.py"]))
        self.assertNotIn("reproduce.py", text)
        self.assertIn("+++ b/src/a.py", text)
        only_new = "diff --git a/reproduce.py b/reproduce.py\nnew file mode 100644\n+print(1)\n"
        self.assertIn("It left no patch to existing files.", arm_text("C", [], failed(patch=only_new)))

    def test_reproduction_is_the_last_run_of_an_own_script(self):
        commands = [
            ("create reproduce.py", ""),
            ("python reproduce.py", "TypeError"),
            ("python setup.py test", "ValueError"),
            ("python reproduce.py", "KeyError"),
        ]
        self.assertEqual(reproduction(failed(commands=commands)), ("python reproduce.py", "KeyError"))

    def test_no_reproduction_when_no_own_script_ran(self):
        self.assertEqual(reproduction(failed(commands=[("python setup.py test", "ValueError")])), ("", ""))


if __name__ == "__main__":
    unittest.main()
