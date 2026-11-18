# recallbench v2: file-level metric, two simple baselines, repository resampling

`SPEC.md` (v1) stays the contract for everything it describes. This file lists only what v2 adds or changes, and why.

## Why v2

v1's main metric, session hit@5, counts a hit when any file named by the top 5 sessions is a file the real fix
changed. A ranking that picks sessions naming many files scores higher without matching the issue any better. Two
checks show the effect on the pinned data:

- A ranking that ignores the query and puts the sessions that name the most files first reaches about the same
  session hit@5 as `hybrid`.
- When every ranking is held to the same number of files, a list of the files that earlier fixes changed most often
  comes close to `hybrid`, without using any session.

v2 makes a fixed-size file list the main metric and reports both baselines next to every v1 ranking. v1 metrics are
still computed and reported unchanged.

## Baselines (`recallbench/retrieve.py`)

They are new `Corpus` methods. They are not added to `RETRIEVERS`, so v1's seven-entry lists and `metrics.json`
keep their shape.

- `most_files(q) -> list[int]`: the pool ordered by `len(session.pointers)`, largest first, with v1's tie order
  (newer task first, then lower session index). It ignores the query and isolates the "names more files" effect.
- `past_fix_files(q) -> list[str]`: every path in the `gold_files` of the same repository's tasks created strictly
  before `q`. Paths are ordered by how many of those tasks changed them, then by the latest such task's
  `created_at`, newest first, then by path from A to Z. It uses no session. A merged fix's
  changed files are known to any memory system with access to the repository's history.

## File-level metric (`recallbench/evaluate.py`, `recallbench/files.py`)

- `first_files(ranked, n)`: walk the ranked memories in order, and each memory's pointers in order (edited first,
  as v1's `Session.pointers`). Keep each path once and stop at `n` paths.
- `file_hit(files, gold)`: 1 if any listed file is a gold file. File recall is v1's `gain(files, gold)`, the share
  of gold files in the list.
- `NS = (1, 5, 10)` and `MAIN_N = 5`. File hit@5 is the main metric.
- Rankings scored: the seven v1 retrievers and `most_files` (through `first_files`), and three file rankers with
  their own lists cut to `n`: `past_fix_files`, `issue_fix_vote` and `vote_prior` (see Vote retriever). These are the same queries as
  v1 (`Corpus.queries()`).
- `COMPARISONS`, each `a` minus `b`: on file hit@5, `vote_prior` vs `issue_fix_vote`, `past_fix_files` and `hybrid`, and `hybrid` vs
  `past_fix_files`, `bm25_issue` and `most_files`. On session hit@5, `hybrid` vs `bm25_issue` and `most_files`.

## Uncertainty (`recallbench/stats.py`)

- v1's paired query bootstrap moves here unchanged: same `SEED`, `BOOTSTRAP_DRAWS` and percentile rule. `run.py`
  uses it, so `metrics.json` is byte-identical to v1.
- New repository bootstrap: draw `R` repositories with replacement from the `R` repositories that have queries,
  with draws `np.random.default_rng(SEED).integers(0, R, (BOOTSTRAP_DRAWS, R))`. Each draw's value is the pooled
  mean over all queries of the drawn repositories (a repository drawn twice counts twice). Same percentile rule.
  It answers "how much does the result depend on which repositories are in the data", which the query bootstrap
  hides when two repositories hold most queries.
- A difference reads as clear only when both 95% intervals exclude zero. When only the query interval excludes zero,
  the reading is "clear over queries, not over repositories".

## Output (`results/files.json`)

`experiment.py --out PATH` writes v1's `metrics.json` to `PATH`, unchanged, and `files.json` in the same folder.
Rates are rounded to 6 places.
- Top level: `ns`, `main_n`, `rankers`, `bootstrap_draws`, `seed`, `dev_choice` (the v1 rule, highest session hit@5
  among `RETRIEVERS`, applied to the development half, or None without development queries), and one summary per
  half: `test`, `dev` and `all`.
- Each summary: `queries`, `repos` (with queries in that half, sorted), `repo_queries`, and, when it has queries,
  `file_hit` and `file_recall` (per ranking, one value per `n`), `file_hit_main_ci` (per ranking, `query_ci` and
  `repo_ci` of file hit@5), `session_hit5` and `files_named5` (per session ranking: v1 hit@5 and the mean number of
  distinct files the top 5 sessions name), `repo_file_hit_main`, `macro_file_hit_main` (mean of the repository
  values), `tokens` (see Memory cost) and `comparisons` (list of `metric`, `a`, `b`, `diff`, `query_ci`,
  `repo_ci`), and `repo_main_comparison` (the first comparison within each repository: `diff` and a `query_ci`
  from a query bootstrap inside that repository). Intervals are computed within the half.
- Top level also has `repos`: every repository of the evaluated tasks, sorted. `demo.py` renders the v1 per-repository
  rows from it, so results of any corpus render, not only the pinned data.

## Memory cost

For each ranking and query: `found5`, the number of gold files among its first 5 files, and the tokens (v1's
four-characters rule) to inject them. The forms are the 5 paths joined by newlines (every ranking), the cards of the
sessions that supply those files (`first_sources`, session rankings only), and those full sessions. Each form also
reports `per_1k = 1000 * mean(found5) / mean(tokens)`.

## Generated README block (`demo.py`)

The block between the v1 markers holds, in order:
- "File level, test half (main result)": a generated headline with the test-half file hit@5 of `vote_prior`, the
  two baselines, `hybrid` and `bm25_issue`. Then the comparison table with both intervals and a reading, a ranking
  table, a per-repository table with a "Mean of repositories" row, and the memory cost table.
- "Development half and all queries": the dev-half choice and its session hit@5 on both halves, then file hit@5 of
  every ranking on each half.
- "Session level, all queries (v1 metrics, unchanged)": the v1 content. A fixed note follows the budget table. Both
  forms are scored on the same pointers, so the card's lead is a budget effect by construction.
- Ranking names in the v2 tables carry no backticks, and per-repository rows put `Queries` last. This keeps v1's
  row-format checks unique. The one-line summary keeps v1's text and adds the test-half file hit@5.
- `render_block(metrics, files)` replaces v1's `render_block(metrics)`. The v1 part is `render_session_block`.

## Misses (`tools/misses.py`, `docs/MISSES.md`)

Every test-half query where `vote_prior`'s first 5 files miss is put in the first category that applies:
- no earlier session or fix names a gold file
- a gold file is only in the fix history
- a top voting session names more than 10 files
- a gold file is named by a voting session but outranked
- a gold file is only in sessions below the voting depth

The doc gives the counts and a seeded sample of 20 misses with the best rank of a gold file.

## Housekeeping

- The v1 acceptance tests live in `tests/acceptance/`, unchanged, and run with `make test`.
- Tests check that `Task` and `Session` are frozen (an open v1 review finding).
- `make lint` passes (ruff check and format). `.github/workflows/ci.yml` runs lint, tests, the data check and a
  fresh experiment and README render that must match the committed files.
- Make targets take `PYTHON` (default `python3`). `make tune` and `make misses` regenerate `results/tuning.json`
  and `docs/MISSES.md`, and CI checks that both match.

## Date split (`recallbench/split.py`), fixed before any tuning

`date_split(tasks, queries) -> (dev, test)`: within each repository, sort its queries by `created_at`. The first
`len // 2` are the development half, and the rest (the later half, which gets the odd one) are the test half. On the
pinned data that gives 297 development and 300 test queries.

Why per repository and not one global date: the repositories cover different years and differ a lot in how easy
they are (pyupgrade is nearly one file, dvc is large). A single global cut date would put most of some repositories
in one half, so the halves would differ by repository mix, not only by time. The per-repository cut keeps every
repository in both halves, and every test query is later than every development query of its own repository.
The pools are unchanged: a test query still sees the sessions of all earlier tasks, including development-half
tasks, as a deployed memory would.

Rule: every new weight or setting, and the v1-style choice of a best retriever, is made on the development half
only. The test half is scored once, after the settings are committed and tagged `v2-settings-frozen`.

## Vote retriever (`recallbench/vote.py`), settings frozen on the development half

Disclosure: the idea (sessions vote for files, fused with the past-fix prior, long file lists down-weighted) came
from a quick check on all 597 queries before the split existed. So the test half is not naive to the idea. It is
naive to the settings, which were chosen on the development half only.

- `votes(ranked, settings)`: the first `depth` sessions of the `hybrid` ranking vote. A session at rank `r` adds
  `spread * weight / (fuse_k + r)` to each file it points to. `spread = min(1, cap / len(pointers))` (1 when `cap`
  is None) and `weight = edited_weight` for an edited file, else 1.
- `vote_files(ranked, prior, settings)`: rank files by votes and take the `past_fix_files` list as the prior. Each
  file scores `(1 - prior_weight) / (fuse_k + vote_rank) + prior_weight / (fuse_k + prior_rank)`, where a list
  that lacks the file adds 0. Ties go by path from A to Z.
- Grid (`tools/tune_vote.py`, 400 settings): `depth` in 5, 10, 20, 50, `cap` in 3, 5, 10, 20, None,
  `edited_weight` in 1, 2, `prior_weight` in 0.2, 0.35, 0.5, 0.65, 0.8, and `fuse_k` in 5, 60.
- Rule: the highest development file hit@5, then the highest development file recall@5, then the first in grid
  order. `results/tuning.json` keeps every setting's development scores, the choice, and the v1-style best
  retriever chosen on the development half (highest session hit@5).
- `VOTE` in `vote.py` holds the chosen settings, and a test checks that it matches `results/tuning.json`. The commit
  that froze them is tagged `v2-settings-frozen`. The test half was scored only after that tag.

## Query-aware fix-history control (added after review)

An independent review pointed out that `past_fix_files` ignores the query, so beating it does not show that sessions
add anything beyond fix history. `issue_fix_vote` answers that. `Corpus.issue_tasks(q)` ranks the same repository's
tasks created strictly before `q` by BM25 between their issue text and `q`'s (one document per task, statistics from
those tasks only, newer task first on ties). Each task's gold files vote as a `Fix`, a voter that names and edits its
gold files, and `vote_files` fuses the votes with `past_fix_files` under the frozen `VOTE` settings. No session is
used and nothing was re-tuned. It was added after the test half had been scored, as a control, not as a candidate.

Leakage tests (`tests/test_leakage.py`): changing every test-half task and session leaves the development tuning
unchanged, while the same change to the other tasks does change it. For a query, changing the query itself, a task
created at the same moment and a later task, with their sessions, leaves its prior, `vote_prior` and
`issue_fix_vote` lists unchanged.
