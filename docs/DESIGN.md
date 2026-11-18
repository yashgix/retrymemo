# Design

## Scope

`recallbench` is an offline retrieval benchmark. It does not call a model, train a ranker or measure end-to-end task success. It measures whether a memory that a retriever would inject points to files that the task's real fix touched, and what those memories cost in tokens.

## Unit of memory

One memory is one agent session, not one task. A session has the issue of its own past task, the agent turns, the files it opened and edited, and whether its patch passed the tests. A task with two sessions contributes two independent memories. This matches the thing a memory layer would store and inject.

A session's pointers are its edited files followed by its opened files, deduplicated. Edited files rank first because a patch is stronger evidence than opening a file, but both are evidence that the session touched a path. Files that the agent's patch added are omitted: scratch scripts say little about the repository.

## Leak-free pool

For query task `q`, the pool contains sessions whose own task is in the same repository as `q` and has a `created_at` strictly earlier than `q`'s. The comparison is string comparison on the fixed timestamp format. Equal timestamps never see each other. This is the same rule a deployed memory would obey: a memory cannot contain the future of the task it is used for.

`queries()` keeps only tasks with enough earlier tasks in their repository (`MIN_HISTORY`), so the pool is not empty or trivially small. The rest of the tasks still supply memories for later queries.

The time order is PR creation time. Sessions were generated later, on each task's own base commit. That is a limitation of the public data, but it does not leak the query task's gold patch into the pool.

## Relevance label

A memory is relevant for a query if `set(memory.pointers) & set(task.gold_files)` is non-empty. `gold_files` are the paths in the task's merged gold patch, excluding test files because the source patch excludes them. This label is objective and reproducible.

It is a proxy. It rewards a memory that names a file the fix touched and does not check whether the memory explains why the file matters. A memory can be useful without naming a gold file, and a memory can name a gold file with misleading text. The benchmark therefore reports ranking quality and cost, not a claim about agent success.

## Retrievers

All retrievers rank the same pool. BM25 statistics (`N`, document frequency, average length) come from the pool only, because a memory system knows only its past. There is one BM25 document per session, including for the issue retriever: a past task with two sessions counts twice.

- `recent`: all scores equal, so the tie order puts the newest tasks' sessions first. It is the "just inject recent history" baseline.
- `bm25_issue`: BM25 between the query issue text and each memory's own past task issue. It tests whether the old issue text alone is enough.
- `bm25_session`: BM25 against the full session text, issue plus agent turns. It tests whether the session's own words help.
- `bm25_paths`: BM25 against the file paths the session points to. It is a weak path-matching baseline.
- `hybrid`: reciprocal rank fusion of the session and path rankings. RRF combines ranks, not raw scores, so the two BM25 scales do not need calibration. The fusion is computed on the whole pool.
- `resolved_only`: the hybrid ranking with unresolved sessions removed. It tests the common product rule "only remember successes".
- `fresh`: the hybrid score multiplied by a fixed decay for each later fix that rewrote a file the memory points to. It tests a simple staleness signal.

Ties go to the memory whose task is later in `tasks` (newer), then to the lower session index. That is deterministic and matches the intuition that, all else equal, a newer memory is more useful. An unknown retriever name raises `ValueError`.

## Memory forms and budget packing

Each memory has two forms. The full form is the past task's issue plus every agent turn. The card form is four lines: issue title, resolved yes/no, edited files, opened files. Costs are estimated as four characters per token.

The budget experiment asks: if the memory layer may spend at most a budget of tokens, which memories fit? `pack` walks the ranking from the top, keeps a memory if its cost fits in the remaining budget, skips it otherwise, and continues to a fixed depth. This is greedy and not optimal packing, but it matches a simple injection policy and makes the comparison between forms clear. A budget hit is 1 if any kept memory points to a gold file.

## Metrics and bootstrap

For each query and retriever the benchmark records hit@1 and hit@5 (any top memory points to a gold file), recall@5 (share of gold files named by the union of the top five), and nDCG@5 (rank-weighted gain). nDCG's ideal DCG uses the best gains in the whole pool, even for `resolved_only`, so a filtered ranking is not rewarded for hiding candidates.

The bootstrap is paired over queries: the same resampled query indices are used for every retriever. That preserves the correlation between retrievers and makes the interval on the paired difference against `bm25_issue` meaningful. The interval is a percentile interval of the resampled mean.

## v2: file-level metric and baselines

Session hit@5 counts any file named by the top 5 sessions, so it grows with how many files those sessions name. v2 (`SPEC-v2.md`) holds every ranking to the same number of distinct files. `first_files` walks the ranking and each session's pointers in order until it has N files. File hit@N and file recall@N score that list, and N = 5 is the main metric.

Two baselines test what a retriever must beat. `most_files` ranks sessions by how many files they name and ignores the query. It measures the "names more files" effect alone. `past_fix_files` ranks the files that earlier fixes in the repository changed, most often first, and uses no session. It measures how far plain file popularity gets.

A second bootstrap resamples whole repositories and pools their queries. Two repositories hold most queries, so the query bootstrap understates how much the result depends on which repositories are in the data. The README calls a difference clear only when both intervals exclude zero.

## Date split and the vote retriever

The queries are split by date within each repository: the earlier half is for development, the later half is the test. A per-repository cut keeps every repository in both halves, so the halves differ by time and not by repository mix. Test queries still see all earlier sessions, as a deployed memory would.

`vote_prior` lets the top `hybrid` sessions vote for the files they name, weighted by rank, and fuses those votes with the past-fix list by reciprocal rank. Its settings were chosen on the development half from a grid of 400 and frozen before the test half was scored. The idea itself came from a look at all queries, so the test half checks the settings, not the idea.

After review, `issue_fix_vote` was added as a control: earlier tasks ranked by issue text vote with their gold files, under the same frozen fusion, with no sessions. It separates what sessions add from what a query-aware use of fix history gives.

The memory cost table compares three ways to inject the same five files: the paths alone, the cards of the sessions that supplied them, or those full sessions. It measures cost per gold file found, not whether the extra text helps an agent.

## Trade-offs

- File overlap is a proxy for usefulness. It is objective but incomplete.
- The sessions are public SWE-agent runs, not a team's memory. They are one or two per task, and some sessions point to no file.
- The repositories are few and uneven. Aggregate numbers are dominated by the largest two, and small per-repository rows are noisy.
- Token costs are character-based estimates, not tokenizer counts.
- The staleness re-ranker is one hand-set rule, not a study of staleness.
- The v1 constants were chosen after a prototype run on all the data. Only the v2 vote settings and the dev-half retriever choice respect the date split.
- Redaction is pattern-based. Free-text names and decorator-like handles can remain.
- No agent was re-run with memories. The benchmark measures retrieval, not end-to-end lift.
