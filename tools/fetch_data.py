"""Rebuild data/tasks.jsonl and data/sessions.jsonl from Nebius's public SWE-bench-extra and SWE-agent-trajectories.

Downloads the 13 pinned parquet files (about 1.1 GB, checked by sha256) into data/raw/, which git ignores, picks the
sessions by the rule in MANIFEST.json, checks the pick against the pinned row list, then writes and verifies the two
files. `--source DIR` reads the parquet files from a local copy instead, and `--verify` only checks the two files;
neither uses the network. Needs only the standard library (Python 3.14).
"""

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
FILES = ("tasks.jsonl", "sessions.jsonl")
MIN_COVERED = 20
TASK_COLUMNS = ("instance_id", "repo", "created_at", "license", "problem_statement", "patch")
ROLE, TEXT = "trajectory.list.element.role", "trajectory.list.element.text"
SESSION_COLUMNS = ("instance_id", "model_name", "target", "exit_status", "generated_patch", ROLE, TEXT)
DIFF = re.compile(r"^diff --git a/\S+ b/(\S+)$", re.MULTILINE)
ADDED = re.compile(r"^diff --git a/\S+ b/(\S+)\nnew file mode", re.MULTILINE)
OPEN_FILE = re.compile(r"\(Open file: ([^)\n]+)\)")
CWD = re.compile(r"\(Current directory: ([^)\n]+)\)")
MENTION = re.compile(r"(?<![\w.@`/\\])@[A-Za-z0-9][A-Za-z0-9-]{0,38}(?![\w.(-])")
REDACT = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"(/(?:home|Users)/|[A-Za-z]:\\\\?Users\\\\?)[^/\\\s'\"]+"), r"\1<user>"),
    (
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
            r"|xox[baprs]-[A-Za-z0-9-]{10,}|hf_[A-Za-z0-9]{30,})\b"
        ),
        "<token>",
    ),
    # The credential shapes of the agent runner's outbound check, so no data line can trip it (fake ones included).
    # Every NAME= is replaced, value or not: in JSON an escaped newline would join it to the next line's text.
    (re.compile(r"([A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|PASSWORD))=[^\s\"']*"), r"<redacted: \1>"),
    (
        re.compile(
            r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
        ),
        "<token>",
    ),
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 24):
            digest.update(block)
    return digest.hexdigest()


def download(source, folder):
    """The source's parquet files in folder (downloaded when missing), each checked against its sha256."""
    paths = []
    for entry in source["files"]:
        path = folder / Path(entry["name"]).name
        if not path.is_file():
            url = f"https://huggingface.co/datasets/{source['dataset']}/resolve/{source['revision']}/{entry['name']}"
            folder.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(url, headers={"User-Agent": "fetch_data"})
            with urllib.request.urlopen(request, timeout=600) as response, open(path, "wb") as out:
                while block := response.read(1 << 24):
                    out.write(block)
        if sha256(path) != entry["sha256"]:
            sys.exit(f"{path} does not match its pinned sha256")
        paths.append(path)
    return paths


# A minimal parquet reader: snappy pages (data page v1), plain or dictionary strings, booleans, at most one list level.
class Thrift:
    """Thrift compact protocol, as parquet uses for its footer and page headers; structs map field id to value."""

    def __init__(self, buf, pos=0):
        self.buf, self.pos = buf, pos

    def varint(self):
        value, self.pos = uvarint(self.buf, self.pos)
        return value

    def value(self, kind):
        if kind in (1, 2):
            return kind == 1
        if kind == 3:
            self.pos += 1
            return self.buf[self.pos - 1]
        if kind in (4, 5, 6):
            v = self.varint()
            return (v >> 1) ^ -(v & 1)
        if kind == 7:
            self.pos += 8
            return None
        if kind == 8:
            n = self.varint()
            self.pos += n
            return self.buf[self.pos - n : self.pos]
        if kind in (9, 10):
            head = self.buf[self.pos]
            self.pos += 1
            n = head >> 4 if head >> 4 != 15 else self.varint()
            item = head & 15
            return [self.value(3) == 1 if item in (1, 2) else self.value(item) for _ in range(n)]
        if kind == 12:
            return self.struct()
        raise ValueError(f"thrift type {kind}")

    def struct(self):
        out, field = {}, 0
        while (head := self.buf[self.pos]) != 0:
            self.pos += 1
            field = field + (head >> 4) if head >> 4 else self.value(5)
            out[field] = self.value(head & 15)
        self.pos += 1
        return out


def uvarint(buf, pos):
    value = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, pos


def snappy(buf):
    size, pos = uvarint(buf, 0)
    out = bytearray()
    while pos < len(buf):
        tag, kind = buf[pos], buf[pos] & 3
        pos += 1
        if kind == 0:
            n = tag >> 2
            if n >= 60:
                n, pos = int.from_bytes(buf[pos : pos + n - 59], "little"), pos + n - 59
            out += buf[pos : pos + n + 1]
            pos += n + 1
            continue
        if kind == 1:
            n, offset, pos = ((tag >> 2) & 7) + 4, ((tag >> 5) << 8) | buf[pos], pos + 1
        else:
            width = 2 if kind == 2 else 4
            n, offset, pos = (tag >> 2) + 1, int.from_bytes(buf[pos : pos + width], "little"), pos + width
        start = len(out) - offset
        while n > 0:
            chunk = out[start : start + min(n, offset)]
            out += chunk
            n -= len(chunk)
            start += len(chunk)
    if len(out) != size:
        raise ValueError("snappy length mismatch")
    return bytes(out)


def hybrid(buf, pos, width, count):
    """`count` values of parquet's RLE / bit-packed hybrid encoding."""
    out = []
    while len(out) < count:
        head, pos = uvarint(buf, pos)
        if head & 1:
            n = (head >> 1) * width
            bits = int.from_bytes(buf[pos : pos + n], "little")
            pos += n
            out += [(bits >> (i * width)) & ((1 << width) - 1) for i in range((head >> 1) * 8)]
        else:
            n = (width + 7) // 8
            out += [int.from_bytes(buf[pos : pos + n], "little")] * (head >> 1)
            pos += n
    return out[:count]


def plain_strings(buf, pos, count):
    out = []
    for _ in range(count):
        n = int.from_bytes(buf[pos : pos + 4], "little")
        out.append(buf[pos + 4 : pos + 4 + n].decode())
        pos += 4 + n
    return out


def leaves(schema):
    """{dotted name: (max definition level, max repetition level, definition level of a list item)} per leaf."""
    out, pos = {}, 1

    def walk(prefix, d, r, item):
        nonlocal pos
        element = schema[pos]
        pos += 1
        kind = element.get(3, 0)
        d, r = d + (kind != 0), r + (kind == 2)
        item = d if kind == 2 else item
        name = [*prefix, element[4].decode()]
        if element.get(5):
            for _ in range(element[5]):
                walk(name, d, r, item)
        else:
            out[".".join(name)] = (d, r, item)

    while pos < len(schema):
        walk([], 0, 0, None)
    return out


def levels(data, pos, max_level, count):
    if not max_level:
        return [0] * count, pos
    size = int.from_bytes(data[pos : pos + 4], "little")
    return hybrid(data, pos + 4, max_level.bit_length(), count), pos + 4 + size


def page_values(data, header, leaf, dictionary, is_bool):
    count, encoding = header[1], header[2]
    max_def, max_rep, _ = leaf
    reps, pos = levels(data, 0, max_rep, count)
    defs, pos = levels(data, pos, max_def, count)
    n = sum(d == max_def for d in defs)
    if encoding in (2, 8):
        width = data[pos]
        values = [dictionary[i] for i in hybrid(data, pos + 1, width, n)] if width else [dictionary[0]] * n
    elif is_bool and encoding == 0:
        bits = int.from_bytes(data[pos:], "little")
        values = [bool((bits >> i) & 1) for i in range(n)]
    elif is_bool and encoding == 3:
        values = [bool(v) for v in hybrid(data, pos + 4, 1, n)]
    elif encoding == 0:
        values = plain_strings(data, pos, n)
    else:
        raise ValueError(f"parquet encoding {encoding}")
    return reps, defs, values


def assemble(rows, reps, defs, values, leaf):
    """Append one page's values to rows: a value per row, or a list per row for a list column."""
    max_def, max_rep, item = leaf
    it = iter(values)
    for r, d in zip(reps, defs, strict=True):
        value = next(it) if d == max_def else None
        if not max_rep:
            rows.append(value)
            continue
        if r == 0:
            rows.append([])
        if d >= item:
            rows[-1].append(value)


def column(buf, meta, leaf):
    start = min(meta.get(11, meta[9]), meta[9])
    pos, end, dictionary, rows = start, start + meta[7], None, []
    while pos < end:
        reader = Thrift(buf, pos)
        header = reader.struct()
        data = buf[reader.pos : reader.pos + header[3]]
        pos = reader.pos + header[3]
        data = snappy(data) if meta[4] == 1 else data
        if header[1] == 2:
            dictionary = plain_strings(data, 0, header[7][1])
        elif header[1] == 0:
            assemble(rows, *page_values(data, header[5], leaf, dictionary, meta[1] == 0), leaf)
        else:
            raise ValueError(f"parquet page type {header[1]}")
    return rows


def row_groups(path, names):
    """Yield one {name: values} per row group of a parquet file, for the named columns."""
    buf = path.read_bytes()
    size = int.from_bytes(buf[-8:-4], "little")
    meta = Thrift(buf, len(buf) - 8 - size).struct()
    found = leaves(meta[2])
    for group in meta[4]:
        chunks = {".".join(p.decode() for p in c[3][3]): c[3] for c in group[1]}
        yield {name: column(buf, chunks[name], found[name]) for name in names}


def read_table(paths, names):
    out = {name: [] for name in names}
    for path in paths:
        for group in row_groups(path, names):
            for name in names:
                out[name] += group[name]
    return [dict(zip(names, row, strict=True)) for row in zip(*out.values(), strict=True)]


def select(index, repo_of, low=MIN_COVERED, high=None):
    """First resolved and first failed session (lowest row) per task, in repos with low to high-1 covered tasks."""
    covered = Counter(repo_of[i] for i in {i for i, _ in index if i in repo_of})
    repos = {r for r, n in covered.items() if n >= low and (high is None or n < high)}
    first = {}
    for row, (instance, resolved) in enumerate(index):
        if repo_of.get(instance) in repos:
            first.setdefault((instance, resolved), row)
    return sorted(repos), sorted(first.values())


def picked_rows(paths, picked):
    """{row index: row} for the picked trajectory rows, read one row group at a time."""
    wanted, out, offset = set(picked), {}, 0
    for path in paths:
        for group in row_groups(path, SESSION_COLUMNS):
            n = len(group["instance_id"])
            for i in range(n):
                if offset + i in wanted:
                    out[offset + i] = {name: group[name][i] for name in SESSION_COLUMNS}
            offset += n
    return out


def mention(match):
    """@handle -> @<user> unless it starts its line, as a Python decorator does."""
    text, start = match.string, match.start()
    return match.group(0) if not text[text.rfind("\n", 0, start) + 1 : start].strip() else "@<user>"


def redact(text):
    for pattern, replacement in REDACT:
        text = pattern.sub(replacement, text)
    return MENTION.sub(mention, text)


def relative(path, root):
    path = path.strip()
    if path.startswith(root + "/"):
        return path[len(root) + 1 :]
    return None if path.startswith("/") or path == "n/a" else path


def observations(row):
    return [t or "" for r, t in zip(row[ROLE], row[TEXT], strict=True) if r == "user"]


def opened_files(texts, root, skip):
    seen = {}
    for text in texts:
        for path in OPEN_FILE.findall(text):
            if (rel := relative(path, root)) is not None and rel not in skip:
                seen.setdefault(rel, None)
    return list(seen)


def session_record(row, row_index):
    """One session; files the agent's own patch adds (scratch scripts such as reproduce.py) are left out."""
    patch, seen = row["generated_patch"] or "", observations(row)
    added = set(ADDED.findall(patch))
    cwd = next((m.group(1).strip() for t in seen if (m := CWD.search(t))), "")
    return {
        "instance_id": row["instance_id"],
        "row": row_index,
        "model": row["model_name"],
        "resolved": bool(row["target"]),
        "exit_status": row["exit_status"],
        "opened": opened_files(seen, cwd, added),
        "edited": sorted(set(DIFF.findall(patch)) - added),
        "turns": [redact(t or "") for r, t in zip(row[ROLE], row[TEXT], strict=True) if r == "ai"],
    }


def task_record(row):
    return {
        "instance_id": row["instance_id"],
        "repo": row["repo"],
        "created_at": row["created_at"],
        "license": row["license"],
        "issue": redact(row["problem_statement"]),
        "gold_files": sorted(set(DIFF.findall(row["patch"]))),
    }


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records))


def build(manifest, raw):
    extra = read_table(download(manifest["tasks_source"], raw / "SWE-bench-extra"), TASK_COLUMNS)
    shards = download(manifest["sessions_source"], raw / "SWE-agent-trajectories")
    index = [(r["instance_id"], r["target"]) for r in read_table(shards, ("instance_id", "target"))]
    repos, picked = select(index, {r["instance_id"]: r["repo"] for r in extra})
    if repos != manifest["repos"] or picked != manifest["session_rows"]:
        sys.exit("the session pick differs from MANIFEST.json")
    rows = picked_rows(shards, picked)
    covered = {rows[r]["instance_id"] for r in picked}
    tasks = sorted(
        (r for r in extra if r["instance_id"] in covered), key=lambda r: (r["repo"], r["created_at"], r["instance_id"])
    )
    order = {t["instance_id"]: i for i, t in enumerate(tasks)}
    sessions = sorted(
        (session_record(rows[r], r) for r in picked), key=lambda s: (order[s["instance_id"]], not s["resolved"])
    )
    write_jsonl(DATA / "tasks.jsonl", map(task_record, tasks))
    write_jsonl(DATA / "sessions.jsonl", sessions)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="check the two data files only; no network")
    parser.add_argument("--source", type=Path, help="a folder with the parquet files as data/raw/ holds them")
    args = parser.parse_args()
    manifest = json.loads((DATA / "MANIFEST.json").read_text())
    if not args.verify:
        build(manifest, args.source or DATA / "raw")
    bad = [name for name in FILES if sha256(DATA / name) != manifest["files"][name]["sha256"]]
    if bad:
        sys.exit(f"{bad} do not match MANIFEST.json")
    print("data matches MANIFEST.json: " + ", ".join(f"{n} ({manifest['files'][n]['rows']} rows)" for n in FILES))


if __name__ == "__main__":
    main()
