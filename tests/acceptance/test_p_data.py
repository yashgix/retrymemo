import dataclasses
import hashlib
import json
import re
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import recallbench
from recallbench.data import DATA_DIR, Session, Task, load

ROOT = Path(recallbench.__file__).resolve().parent.parent
SHA256 = {
    "tasks.jsonl": "2a9eb4ac7e59369aafb77bd907226fb1e8e5e5b393432dac3ec5c99a9e7fa402",
    "sessions.jsonl": "dc4c0c0fa0f94a6706fac533dbcd20d18f619a17ef1bb962ae415c8e0cadce1e",
    "LICENSE-CC-BY-4.0.txt": "9ba9550ad48438d0836ddab3da480b3b69ffa0aac7b7878b5a0039e7ab429411",
    "SWE-agent-trajectories-README.md": "81cbf0ee9f00e45aff9eb90ebe7f59e714ca7135b9b78a90d5227f866021f564",
    "SWE-bench-extra-README.md": "1ddf148e05cce5e300b6b94d77bdfbc9f1a5424f2fb135d0591f3a8b6a4a1bdb",
}
REPOS = {
    "PyCQA/pyflakes": 20,
    "Textualize/textual": 23,
    "asottile/pyupgrade": 55,
    "iterative/dvc": 258,
    "oasis-open/cti-python-stix2": 23,
    "pydantic/pydantic": 200,
    "reframe-hpc/reframe": 20,
    "tobymao/sqlglot": 78,
}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
HOME = re.compile(r"(?:/home/|/Users/|Users\\)(?!<user>)[A-Za-z0-9_.-]+")
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b")
# The agent runner's outbound secret check, copied as is: no repo file may match it, raw or JSON-decoded.
RUNNER_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|PASSWORD)=[^\s\"']{8,}"
)
MENTION = re.compile(r"(?<![\w.@`/\\])@[A-Za-z0-9][A-Za-z0-9-]{0,38}(?![\w.(-])")


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict | list):
        for item in value.values() if isinstance(value, dict) else value:
            yield from strings(item)


def mid_line_mentions(text):
    return [m.group(0) for m in MENTION.finditer(text) if text[text.rfind("\n", 0, m.start()) + 1 : m.start()].strip()]


class Files(unittest.TestCase):
    def test_data_dir(self):
        self.assertEqual(DATA_DIR, ROOT / "data")
        self.assertEqual(recallbench.DATA_DIR, DATA_DIR)

    def test_checksums(self):
        manifest = json.loads((DATA_DIR / "MANIFEST.json").read_text())
        for name, digest in SHA256.items():
            self.assertEqual(hashlib.sha256((DATA_DIR / name).read_bytes()).hexdigest(), digest, name)
        for name in ("tasks.jsonl", "sessions.jsonl"):
            self.assertEqual(manifest["files"][name]["sha256"], SHA256[name])
        self.assertEqual(len(manifest["session_rows"]), 812)
        self.assertEqual(manifest["repos"], sorted(REPOS))

    def test_no_file_matches_the_runner_secret_check(self):
        found = []
        for path in sorted(ROOT.rglob("*")):
            parts = path.relative_to(ROOT).parts
            if not path.is_file() or parts[0] in (".git", "results") or "__pycache__" in parts:
                continue
            text = path.read_bytes().decode("utf-8", "replace")
            found += [(path.name, m.group(0)[:30]) for m in RUNNER_SECRET.finditer(text)]
            if path.suffix in (".json", ".jsonl"):
                docs = (
                    [json.loads(line) for line in text.split("\n") if line.strip()]
                    if path.suffix == ".jsonl"
                    else [json.loads(text)]
                )
                found += [
                    (path.name, m.group(0)[:30]) for d in docs for v in strings(d) for m in RUNNER_SECRET.finditer(v)
                ]
        self.assertEqual(found[:5], [])


class Load(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks, cls.sessions = load()

    def test_types_and_counts(self):
        tasks, sessions = load(str(DATA_DIR))
        self.assertIsInstance(tasks, list)
        self.assertIsInstance(sessions, list)
        self.assertEqual((len(tasks), len(sessions)), (677, 812))
        self.assertEqual(
            [f.name for f in dataclasses.fields(Task)],
            ["instance_id", "repo", "created_at", "license", "issue", "gold_files"],
        )
        self.assertEqual(
            [f.name for f in dataclasses.fields(Session)],
            ["instance_id", "row", "model", "resolved", "exit_status", "opened", "edited", "turns"],
        )
        self.assertIsInstance(tasks[0].gold_files, tuple)
        self.assertTrue(all(isinstance(getattr(sessions[0], k), tuple) for k in ("opened", "edited", "turns")))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            tasks[0].repo = "x"

    def test_real_facts(self):
        self.assertEqual(Counter(t.repo for t in self.tasks), REPOS)
        self.assertEqual(sum(s.resolved for s in self.sessions), 138)
        self.assertEqual(sum(not s.pointers for s in self.sessions), 68)
        self.assertTrue(all(t.gold_files for t in self.tasks))
        self.assertEqual(Counter(s.model for s in self.sessions)["swe-agent-llama-70b"], 784)

    def test_order(self):
        keys = [(t.repo, t.created_at, t.instance_id) for t in self.tasks]
        self.assertEqual(keys, sorted(keys))
        position = {t.instance_id: i for i, t in enumerate(self.tasks)}
        order = [(position[s.instance_id], not s.resolved) for s in self.sessions]
        self.assertEqual(order, sorted(order))
        per_task = Counter((s.instance_id, s.resolved) for s in self.sessions)
        self.assertEqual(max(per_task.values()), 1)
        self.assertEqual({s.instance_id for s in self.sessions}, set(position))

    def test_no_personal_data(self):
        texts = [t.issue for t in self.tasks] + [x for s in self.sessions for x in s.turns]
        for pattern in (EMAIL, HOME, TOKEN):
            found = [m.group(0) for t in texts for m in pattern.finditer(t)]
            self.assertEqual(found[:5], [], pattern.pattern)
        self.assertEqual([m for t in texts for m in mid_line_mentions(t)][:5], [])


class Synthetic(unittest.TestCase):
    def test_pointers(self):
        s = Session("a-1", 0, "m", True, "submitted", ("b.py", "a.py", "c.py"), ("a.py", "d.py"), ())
        self.assertEqual(s.pointers, ("a.py", "d.py", "b.py", "c.py"))

    def test_orphan_session(self):
        task = {
            "instance_id": "a-1",
            "repo": "o/a",
            "created_at": "2020-01-01 00:00:00+00:00",
            "license": "mit",
            "issue": "x",
            "gold_files": ["a.py"],
        }
        session = {
            "instance_id": "a-2",
            "row": 3,
            "model": "m",
            "resolved": False,
            "exit_status": "submitted",
            "opened": [],
            "edited": [],
            "turns": ["t"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            write_jsonl(Path(tmp) / "tasks.jsonl", [task])
            write_jsonl(Path(tmp) / "sessions.jsonl", [dict(session, instance_id="a-1")])
            tasks, sessions = load(Path(tmp))
            self.assertEqual(tasks[0].gold_files, ("a.py",))
            self.assertEqual(sessions[0].turns, ("t",))
            write_jsonl(Path(tmp) / "sessions.jsonl", [session])
            with self.assertRaises(ValueError):
                load(tmp)
