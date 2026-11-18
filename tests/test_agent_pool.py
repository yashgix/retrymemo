import unittest

from agent.check_pool import changed_lines, issue_names_fix_file


class IssueNamingTest(unittest.TestCase):
    def test_path_file_name_and_module_count(self):
        gold = ("src/pkg/io/reader.py",)
        self.assertTrue(issue_names_fix_file("see pkg/io/reader.py line 3", gold))
        self.assertTrue(issue_names_fix_file("crash in reader.py", gold))
        self.assertTrue(issue_names_fix_file("from pkg.io.reader import load", gold))
        self.assertFalse(issue_names_fix_file("load() crashes on empty files", gold))

    def test_package_init_needs_a_dotted_name(self):
        gold = ("pkg/sub/__init__.py",)
        self.assertFalse(issue_names_fix_file("pkg crashes in __init__.py", gold))
        self.assertTrue(issue_names_fix_file("import pkg.sub fails", gold))


class ChangedLinesTest(unittest.TestCase):
    def test_counts_non_blank_changes_only(self):
        patch = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a = 1\n+a = 2\n+\n context\n"
        self.assertEqual(changed_lines(patch), 2)


if __name__ == "__main__":
    unittest.main()
