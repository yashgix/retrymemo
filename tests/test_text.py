import unittest

from recallbench.data import Session, Task
from recallbench.text import card, session_text, title, tokens, words


def task(issue="Fix the crash", gold_files=("pkg/x.py",)):
    return Task("org__repo-1", "org/repo", "2024-01-01 00:00:00+00:00", "MIT", issue, gold_files)


def session(resolved=True, opened=("pkg/x.py",), edited=("pkg/x.py",), turns=("look", "patch")):
    return Session("org__repo-1", 0, "swe-agent-llama-70b", resolved, "submitted", opened, edited, turns)


class WordsTest(unittest.TestCase):
    def test_spec_example(self):
        self.assertEqual(
            words("BaseModel.parse_obj() in dvc/repo/index.py, x"),
            ["base", "model", "parse", "obj", "in", "dvc", "repo", "index", "py"],
        )

    def test_splits_camel_case(self):
        self.assertEqual(words("fooBarBaz"), ["foo", "bar", "baz"])
        self.assertEqual(words("XMLHttpRequest"), ["xmlhttp", "request"])

    def test_splits_on_underscores_and_punctuation(self):
        self.assertEqual(words("parse_obj"), ["parse", "obj"])
        self.assertEqual(words("a-b/c.d"), [])
        self.assertEqual(words("ab-cd/ef.gh"), ["ab", "cd", "ef", "gh"])

    def test_drops_one_character_terms(self):
        self.assertEqual(words("a b cd 1 2 33"), ["cd", "33"])

    def test_keeps_duplicates_in_order(self):
        self.assertEqual(words("bug bug fix"), ["bug", "bug", "fix"])

    def test_empty_text(self):
        self.assertEqual(words(""), [])


class TokensTest(unittest.TestCase):
    def test_about_four_characters_per_token(self):
        self.assertEqual(tokens(""), 0)
        self.assertEqual(tokens("a"), 1)
        self.assertEqual(tokens("abcd"), 1)
        self.assertEqual(tokens("abcde"), 2)
        self.assertEqual(tokens("a" * 400), 100)


class TitleTest(unittest.TestCase):
    def test_first_line_of_the_stripped_issue(self):
        self.assertEqual(title(task("Fix the crash\n\nbody text")), "Fix the crash")
        self.assertEqual(title(task("  \n\nReal title\nbody")), "Real title")
        self.assertEqual(title(task("  Fix the crash  ")), "Fix the crash")

    def test_empty_issue(self):
        self.assertEqual(title(task("")), "")
        self.assertEqual(title(task("   \n\n")), "")


class SessionTextTest(unittest.TestCase):
    def test_issue_then_turns(self):
        self.assertEqual(
            session_text(task("Fix the crash\nbody"), session(turns=("look", "patch"))),
            "Fix the crash\nbody\nlook\npatch",
        )

    def test_no_turns_is_the_issue(self):
        self.assertEqual(session_text(task("Fix the crash"), session(turns=())), "Fix the crash")


class CardTest(unittest.TestCase):
    def test_resolved_card(self):
        self.assertEqual(
            card(task("Fix the crash\nbody"), session(True, ("c.py",), ("a.py", "b.py"))),
            "Fix the crash\nresolved: yes\nedited: a.py, b.py\nopened: c.py",
        )

    def test_unresolved_card_with_no_files(self):
        self.assertEqual(
            card(task("Fix the crash"), session(False, (), ())),
            "Fix the crash\nresolved: no\nedited: \nopened: ",
        )
