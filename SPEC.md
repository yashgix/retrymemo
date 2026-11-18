# recallbench v1: which past agent sessions point a new task to the files its real fix touched

## Why
A team that uses coding agents produces hundreds of agent sessions. A memory layer keeps them and, when a new task
starts, injects the few that look relevant into the agent's context. Whether that helps depends on one step that is
rarely measured on its own: **recall**. Given a new task, does the memory pick past sessions that point the agent to
the right code, and how many tokens does it spend to do so?

This project measures recall offline, on public data, with an objective label and no model calls. Public SWE-agent
runs stand in for a team's past sessions. For each new task (a real GitHub issue from SWE-bench-extra), the memory
pool is the sessions of the **same repository's earlier tasks** (created strictly before it, so no memory knows its
own future). A memory counts as relevant when a file it points to (a file the session opened or edited) is one the
task's **real fix** (the merged gold patch) touched. Seven rankings are compared: five retrievers, from "the 5 most recent sessions"
to a fusion of text and path matching, and two re-rankers of the fusion (keep only sessions that solved their task;
down-weight memories whose files were rewritten by later fixes). A token-budget curve shows what each memory form costs: the
full session text versus a short card (title, outcome, file list).

Expected shape of the result (from a prototype; the build's experiment produces the real numbers): over 597 tasks
in 8 repositories, hit@5 (a top-5 memory names a file of the real fix) is about 37% for the 5 most recent sessions,
49% for BM25 on the past issue text and 64% for the fused retriever; keeping only resolved sessions drops it to about
47%; the staleness re-ranker drops it to about 41%. A 37-token card carries the same file pointers as a
3,700-token session, so under a 500-token budget cards reach about 72% while full sessions reach about 2%.

**What it proves.** That the author can turn public agent logs into a reproducible recall benchmark with a
leak-free time split, an objective relevance label and a cost axis, and can report honestly which retrievers and
re-rankers help, which hurt, and by how much (with confidence intervals). The retrievers (BM25, reciprocal rank
fusion) are standard and cited; the benchmark is the contribution.

## Data (pinned, already in this build's repo; no network)
- `data/tasks.jsonl` (677 rows, 1,330,759 bytes) and `data/sessions.jsonl` (812 rows, 11,218,920 bytes) were
  derived by `tools/fetch_data.py` from two CC-BY-4.0 Hugging Face datasets by Nebius: `nebius/SWE-bench-extra`
  (revision `11dcbfb`, 6,376 tasks) and `nebius/SWE-agent-trajectories` (revision `68195a1`, 80,036 SWE-agent runs).
  `data/MANIFEST.json` records the revisions, each parquet file's size and sha256, the selection rule, the 812
  picked trajectory rows, the redaction rule and the sha256 of every data file. `data/LICENSE-CC-BY-4.0.txt` is the
  license and the two `*-README.md` files are the dataset cards. These files are fixed: never change, move or
  regenerate them, and never fetch anything.
- Selection (already applied): the SWE-bench-extra tasks of every repository with at least 20 tasks that have a
  trajectory (8 repositories); per task, the first resolved and the first failed trajectory by row (when they exist).
- How the labels and pointers were found (by `tools/fetch_data.py`, no judgment by hand): `gold_files` are the
  `b/` paths of the `diff --git` headers of each task's gold patch (SWE-bench-extra's `patch`, which leaves out test
  files); `edited` the same for the session's `generated_patch`; `opened` the paths in SWE-agent's
  `(Open file: ...)` status lines, made relative to the first `(Current directory: ...)`; `resolved` is the
  dataset's `target` (the run's patch passed the task's tests).
- `tasks.jsonl`, one JSON object per line, ordered by `(repo, created_at, instance_id)`: `instance_id`, `repo`,
  `created_at` (PR creation time, `"YYYY-MM-DD HH:MM:SS+00:00"`, so string order is time order), `license` (the
  source repository's), `issue` (the issue title and body), `gold_files` (sorted list of the paths the real fix
  changed, never empty).
- `sessions.jsonl`, ordered by their task's position in `tasks.jsonl`, the resolved session first: `instance_id`,
  `row` (the trajectory's row in the source dataset), `model` (`swe-agent-llama-70b` 784, `-8b` 19, `-405b` 9),
  `resolved` (bool; 138 true), `exit_status`, `opened` (repo-relative files the agent opened, first-open order),
  `edited` (sorted files its patch changed), `turns` (the agent's own turns: reasoning and commands, not tool
  output). Files the agent's patch added (scratch scripts such as `reproduce.py`) are left out of both lists. Every
  task has one or two sessions; 68 sessions point to no file at all.
- Repositories and task counts: `PyCQA/pyflakes` 20, `Textualize/textual` 23, `asottile/pyupgrade` 55,
  `iterative/dvc` 258, `oasis-open/cti-python-stix2` 23, `pydantic/pydantic` 200, `reframe-hpc/reframe` 20,
  `tobymao/sqlglot` 78.
- Personal data was redacted in `issue` and `turns`: emails (`<email>`), user names in home paths (`/home/<user>`,
  `/Users/<user>`), credential-like tokens (`<token>`), every `NAME=` whose name ends in `TOKEN`, `SECRET`, `API_KEY` or `PASSWORD`
  (`<redacted: NAME>`, value included; 142 fake `APP_PASSWORD=` lines in agent turns), and @handles that do not start their line (`@<user>`;
  decorators at line start are kept).
- `recallbench.DATA_DIR` is the repo's `data/` folder (`Path(__file__).resolve().parent.parent / "data"`, defined
  in `recallbench/data.py` and imported in `recallbench/__init__.py`).

## Stack
Python 3.14: the standard library plus NumPy (for the bootstrap). Package `recallbench/`,
tests with `unittest` in `tests/`, ruff with line length 120. `Makefile` and `pyproject.toml` are already in the
repo (`make test`, `make lint`, `make data-check`, `make experiment`, `make readme`). The full test suite must stay
well under 5 minutes. No model or API calls anywhere.

## What to build
All names below are required. Functions take and return plain Python values unless stated.

### Data (`recallbench/data.py`)
- `Task`: frozen dataclass, fields in this order: `instance_id`, `repo`, `created_at`, `license`, `issue` (str),
  `gold_files` (tuple of str).
- `Session`: frozen dataclass, fields in this order: `instance_id`, `row` (int), `model`, `resolved` (bool),
  `exit_status`, `opened`, `edited`, `turns` (tuples of str). Property `pointers`: the files the session points to,
  `tuple(dict.fromkeys(edited + opened))` (edited first, each file once).
- `load(data_dir=DATA_DIR) -> tuple[list[Task], list[Session]]`: `data_dir` may be a `str` or a `Path`; reads
  `tasks.jsonl` and `sessions.jsonl` (UTF-8) in file order, JSON lists become tuples. Raises `ValueError` if a
  session's `instance_id` is not a task.

### Text (`recallbench/text.py`)
- `words(text) -> list[str]`: (1) `re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)` (split camelCase), (2) lowercase,
  (3) `re.findall(r"[a-z0-9]+", ...)`, (4) keep terms of 2 or more characters, in order, duplicates kept. Example:
  `words("BaseModel.parse_obj() in dvc/repo/index.py, x")` is
  `["base", "model", "parse", "obj", "in", "dvc", "repo", "index", "py"]`.
- `tokens(text) -> int`: `(len(text) + 3) // 4` (about four characters per token).
- `title(task) -> str`: the first line of `task.issue.strip()`, stripped (`""` when the issue is empty).
- `session_text(task, session) -> str`: `"\n".join((task.issue, *session.turns))`, the **full** memory form.
- `card(task, session) -> str`: the **card** form, four lines joined by `"\n"`: `title(task)`,
  `"resolved: yes"` or `"resolved: no"`, `"edited: " + ", ".join(session.edited)`,
  `"opened: " + ", ".join(session.opened)`.
  In both forms `task` is the session's own (past) task.

### BM25 (`recallbench/bm25.py`)
- `K1 = 1.2`, `B = 0.75`.
- `bm25(query, docs) -> list[float]`: `query` is a list of terms (duplicates count once), `docs` a list of
  `collections.Counter` (term to count; a doc's length is the sum of its counts). With `N = len(docs)`, `avgdl` the
  mean length, `df(t)` the number of docs containing `t`: `idf(t) = ln(1 + (N - df + 0.5) / (df + 0.5))` and
  `score(d) = sum over distinct query terms t in d of idf(t) * tf * (K1 + 1) / (tf + K1 * (1 - B + B * len(d) /
  avgdl))`. Each score is rounded with `round(score, 9)` so that ties are exact. All zeros when `avgdl` is 0;
  `[]` for no docs.

### Retrieval (`recallbench/retrieve.py`)
- `MIN_HISTORY = 10`, `RRF_K = 60`, `STALE_DECAY = 0.8`, `RETRIEVERS = ("recent", "bm25_issue", "bm25_session",
  "bm25_paths", "hybrid", "resolved_only", "fresh")`.
- `Corpus(tasks, sessions)` keeps the two lists as the attributes `tasks` and `sessions`, and builds each task's
  issue `Counter` and each session's two `Counter`s (session text, pointer paths) **once, in `__init__`**: never
  re-tokenise per query, or the experiment becomes several times slower. Below, `q` is a task index and memories
  are session indices.
  - `pool(q) -> list[int]`: the sessions whose task has the same `repo` as task `q` and a `created_at` strictly
    earlier (string comparison), in file order. Tasks created at the same moment never see each other.
  - `queries() -> list[int]`: the task indices, in order, with at least `MIN_HISTORY` earlier tasks in their repo.
  - `later_fixes(q, i) -> int`: the number of tasks `j` in `q`'s repo with `created_at` strictly between that of
    session `i`'s task and that of `q`, whose `gold_files` share a file with session `i`'s `pointers`.
  - `rank(name, q) -> list[int]`: the pool ranked by retriever `name`; `ValueError` for an unknown name.
- Ranking: higher score first; ties go to the memory whose task comes later in `tasks` (newer), then to the lower
  session index. The BM25 query is always `words(task q's issue)`. Scores per retriever:
  - `recent`: every score 0, so the newest tasks' sessions come first.
  - `bm25_issue`: BM25 against the words of each memory's **past task's issue**.
  - `bm25_session`: BM25 against the words of `session_text`.
  - `bm25_paths`: BM25 against `words(" ".join(session.pointers))` (the file paths only).
  - `hybrid`: reciprocal rank fusion of the `bm25_session` and `bm25_paths` rankings: `round(1 / (RRF_K + ra) + 1 /
    (RRF_K + rb), 9)`, with `ra`, `rb` the memory's 1-based positions in those two rankings of the pool.
  - `resolved_only`: the `hybrid` ranking (computed on the whole pool) with only resolved sessions kept, in order.
  - `fresh`: `round(h * STALE_DECAY ** later_fixes(q, i), 9)`, `h` the memory's `hybrid` score (staleness
    re-ranker: a memory loses weight for each later fix that rewrote a file it points to).
  BM25 statistics (`N`, `df`, `avgdl`) come from the pool alone, as a memory system knows only its past. There is
  one BM25 document per memory (session), also for `bm25_issue`: a past task with two sessions counts twice in `N`,
  `df` and `avgdl`.

### Scoring (`recallbench/evaluate.py`)
`ranked` is a list of pointer tuples in rank order (one per memory) and `gold` the task's `gold_files`.
- `KS = (1, 5)`, `BUDGETS = (500, 1000, 2000, 4000, 8000, 16000)`, `PACK_DEPTH = 20`.
- `gain(pointers, gold) -> float`: `len(set(pointers) & set(gold)) / len(gold)`.
- `hit_at(ranked, gold, k) -> int`: 1 if any of the first `k` memories points to a gold file, else 0.
- `recall_at(ranked, gold, k) -> float`: the share of gold files named by the union of the first `k` memories.
- `ndcg_at(ranked, pool, gold, k) -> float`: `DCG = sum(gain_i / log2(i + 1))` over positions `i = 1..k`; the ideal
  DCG uses the `k` largest gains of `pool` (every candidate's pointer tuple, the whole pool even for
  `resolved_only`); `0.0` when the ideal is 0.
- `pack(costs, budget, depth=PACK_DEPTH) -> list[int]`: walk the first `depth` costs in rank order and keep each
  memory whose cost still fits in what is left of the budget (a memory that does not fit is skipped, the walk goes
  on); returns the kept positions.

### Experiment (`recallbench/run.py`, `experiment.py --out PATH [--data DIR]`)
`experiment.py` and `demo.py` sit at the repo root, next to `recallbench/`. `experiment.py` calls
`recallbench.run.main()`.
- `SEED = 20261114`, `BOOTSTRAP_DRAWS = 1000`.
- For every query `q` (from `queries()`) and retriever, with `ranked = rank(name, q)` and costs
  `tokens(session_text(...))` (full) and `tokens(card(...))` (card) of each memory with its own past task:
  `hit1`, `hit5`, `recall5`, `ndcg5`; `full_tokens5` and `card_tokens5` (the summed cost of the first 5 ranked
  memories, fewer when the ranking is shorter); and per budget and form, a budget hit: 1 if any memory that
  `pack(costs_in_rank_order, budget)` keeps points to a gold file.
- Bootstrap over queries (paired, the same draws for every retriever): `draws =
  np.random.default_rng(SEED).integers(0, n_queries, (BOOTSTRAP_DRAWS, n_queries))`; for a per-query array `v`,
  `means = np.sort(v[draws].mean(axis=1))`, `lo = means[BOOTSTRAP_DRAWS // 40]`, `hi = means[-1 - BOOTSTRAP_DRAWS //
  40]` (the 2.5th and 97.5th percentiles).
- `metrics(tasks, sessions) -> dict` returns exactly these 31 keys; floats are rounded to 6 places inside
  `metrics()`, counts are ints:
  - Numbers: `tasks` (677), `sessions` (812), `resolved_sessions` (138), `repos` (8), `queries` (597),
    `answerable` (queries where some pool memory points to a gold file; 510), `min_history` (10), `bootstrap_draws`
    (1000), `mean_pool` (mean pool size over queries), `mean_full_tokens` and `mean_card_tokens` (mean cost over all
    812 sessions), `best_retriever` (an int: the position in `RETRIEVERS` of the highest `hit5`, first on ties), `budgets`
    (the list `[500, 1000, 2000, 4000, 8000, 16000]`).
  - Per retriever, lists of 7 in `RETRIEVERS` order, means over queries: `hit1`, `hit5`, `recall5`, `ndcg5`,
    `full_tokens5`, `card_tokens5`; `hit5_lo`, `hit5_hi` (bootstrap of `hit5`); `hit5_vs_issue`,
    `hit5_vs_issue_lo`, `hit5_vs_issue_hi` (mean and bootstrap of the per-query `hit5` minus `bm25_issue`'s, all
    `0.0` for `bm25_issue`); `full_tokens_per_hit` and `card_tokens_per_hit` (`*_tokens5 / hit5`, `0.0` when
    `hit5` is 0: tokens injected per task that gets a correct pointer).
  - Per retriever and budget, flat lists of 42, index `retriever * 6 + budget`: `full_budget_hit`,
    `card_budget_hit` (mean budget hit).
  - Per repository, repositories in `sorted()` order (the order above): `repo_queries`, `repo_answerable` (8 ints
    each) and `repo_hit5` (flat, 56 = 7 × 8, index `retriever * 8 + repo`; `0.0` for a repo without queries).
- `main(argv=None)`: argparse with `--out` (default `results/metrics.json`) and `--data` (default `DATA_DIR`).
  If `--data` has no `tasks.jsonl`, exit with `SystemExit` and a non-zero code (`parser.error`). Otherwise load,
  compute `metrics` and write them to `--out` (parent folders created) as JSON with `sort_keys=True`, `indent=1`
  and a final newline.
- Two runs give byte-identical files. The reference takes about 15 seconds and writes about 4 KB.

### Generated docs (`demo.py`)
`python demo.py [--results DIR]` prints one line from `DIR/metrics.json` (default `results`): hit@5 of `recent`,
`bm25_issue` and the best retriever (percentages as below), and the best retriever's tokens per hit for cards and
for full sessions.
`python demo.py --readme README.md --out PATH [--results DIR]` writes to PATH the whole README with the block between
the lines `<!-- results:start -->` and `<!-- results:end -->` (both marker lines kept, each on its own line)
rendered from the metrics; text outside the block is unchanged. `main(argv=None)`, called under
`if __name__ == "__main__":` (tests load `demo.py` as a module); `--readme` without `--out` is an argparse error. Percentages have one decimal (`64.2%`), points a sign and one decimal (`+15.2`), nDCG three
decimals, tokens plain integers (`f"{v:.0f}"`). Each table's separator row is `|---|` once per column, no spaces
(`|---|---|---|` for three columns), and every row starts with `| ` and its first cell. The block holds:
- one paragraph: queries, repositories, tasks, sessions and resolved sessions, mean pool size, answerable queries,
  and the mean full-session and card costs;
- a retriever table with one row per retriever in `RETRIEVERS` order: name in backticks (the best one written
  ``**`hybrid`** (best)``), hit@1, hit@5 with its 95% interval (`64.2% (60.5%-68.0%)`), recall@5, nDCG@5, hit@5
  versus `bm25_issue` in points with its interval (`+15.2 (+11.7 to +19.1)`), tokens@5 full, tokens@5 card;
- a budget table, one row per budget (the budget as a plain integer first): hit for `recent` full and card, and for
  the best retriever full and card;
- a repository table, one row per repository (name as in the data), six cells: the name, `repo_queries`,
  `repo_answerable`, and hit@5 of `recent`, `bm25_issue` and the best retriever, exactly like
  `| iterative/dvc | 248 | 195 | 16.9% | 26.2% | 47.6% |`. Repository names come from `load()`; numbers only from
  the metrics.

The runner renders it after the experiment. No result number is ever typed by hand into the README or docs.
Exempt: fixed facts of the pinned data and the code's constants as this spec states them (for example 677 tasks,
68 sessions without pointers, 248 dvc queries, the 0.8 decay); every measured result (hit rates, intervals, tokens
per hit, budget curves) appears only in the generated block, and the prose refers to it.

## Deliverables beyond code
- `README.md` (it must contain the `<!-- results:start -->` and `<!-- results:end -->` lines that `demo.py` fills):
  - The question in plain words (which past sessions should a memory inject, judged by whether they point to the
    files the real fix touched) and why it matters for teams whose agents share memory.
  - The data: the two Nebius datasets (CC-BY-4.0, attribution to Nebius, links, revisions), the per-repository
    license, the Llama 3.1 notice for model outputs from the dataset card, the CC BY 4.0 change notice (this is a
    subset with derived fields and redacted text, not the original datasets), that no model is trained on these
    outputs here, the selection and redaction rules, and
    how to rebuild (`python tools/fetch_data.py`, about 1.1 GB download; `--verify` checks the pinned files).
  - How to run: `make test`, `python experiment.py --out results/metrics.json`, `python demo.py`; the generated
    results block; a short "what this shows" paragraph that reads the numbers only through the generated block.
    "Best" is picked after the fact on this data; say so.
  - Prior art, stated plainly: BM25 (Robertson and Zaragoza, "The Probabilistic Relevance Framework: BM25 and
    Beyond", 2009); reciprocal rank fusion (Cormack, Clarke and Buettcher, SIGIR 2009); nDCG (Järvelin and
    Kekäläinen, 2002); SWE-bench (Jimenez et al., arXiv 2310.06770) and SWE-agent (Yang et al., arXiv 2405.15793);
    file-level localization as a step of agentic repair (Agentless, Xia et al., arXiv 2407.01489). None of the
    retrieval methods is new; the time-split recall benchmark on public sessions is the point.
- `docs/DESIGN.md`: the unit of memory (one session), the leak-free pool, the relevance label and why file overlap
  is a proxy, each retriever and re-ranker, the two memory forms and the budget packing, the bootstrap, trade-offs.
- Limits, stated plainly in the README:
  - File overlap with the real fix is a proxy for "useful context"; a memory can help (a decision, a pitfall)
    without naming a gold file, or name one and still mislead. No agent was re-run with the memories.
  - The sessions are SWE-agent runs by Nebius's models on old commits, not a real team's sessions; one or two per
    task; 68 of 812 point to no file. Pointer lists include files an agent merely opened.
  - Time order is the PR creation time; sessions were generated later on each task's own base commit.
  - 8 Python repositories, dominated by dvc (248 queries) and pydantic (190); per-repository numbers for the small
    repositories rest on 10-13 queries.
  - Tokens are estimated at four characters per token, not with a model's tokenizer.
  - The staleness re-ranker tested here is one simple rule (0.8 per later fix); its result says nothing about
    staleness signals in general.
  - No held-out split: the 0.8 decay, the hybrid's two parts, `RRF_K`, `MIN_HISTORY`, the budgets and the card
    format were set after a prototype run on this same data, and the retriever called best was picked on it.
  - Personal data was redacted by pattern; free-text names in issues may remain, and @handles at the start of a
    line are kept (the rule that protects Python decorators), so some GitHub handles remain.
- Formatter and linter clean (ruff, `make lint`).
- Links to cite (use these exactly; the build is offline): nebius/SWE-bench-extra
  https://huggingface.co/datasets/nebius/SWE-bench-extra ; nebius/SWE-agent-trajectories
  https://huggingface.co/datasets/nebius/SWE-agent-trajectories ; CC BY 4.0
  https://creativecommons.org/licenses/by/4.0/ ; Llama 3.1 License https://www.llama.com/llama3_1/license/ ;
  SWE-bench https://arxiv.org/abs/2310.06770 ;
  SWE-agent https://arxiv.org/abs/2405.15793 ; Agentless https://arxiv.org/abs/2407.01489. Cite no other URL.

## Out of scope for v1
Re-running agents with injected memories, any model or embedding call, learned rankers, other memory sources (PRs,
Slack, tickets), more repositories or sessions per task, and query rewriting.


## Fixed files (kept by the runner)

These files are part of the given project and are restored before every commit, so never write them: `tools/fetch_data.py`, `data/MANIFEST.json`.


## Runtime (set by the runner)

- Python environment: Python 3.14 with NumPy 2.5.4, SciPy 1.18.1 and matplotlib 3.11.2 (Agg backend, files only).
After the final review the runner runs `python experiment.py --out PATH` twice in that Python environment with every environment above readable (10-minute limit each), requires identical metrics, and commits them as results/metrics.json.

