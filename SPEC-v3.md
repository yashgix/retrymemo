# recallbench v3: are sessions worth more than a well-indexed fix history? (pre-registered)

`SPEC.md` and `SPEC-v2.md` stay unchanged. This file is written and tagged `v3-method-frozen` before any task outside
`data/tasks.jsonl` is read. Nothing below changes after the tag.

## Why v3

v2's test half: `vote_prior` (sessions vote for files) 52.7% file hit@5 against `issue_fix_vote` (past fixes found by
issue text) 49.7%, +3.0, not clear. Diagnosis on the existing data (post hoc, an explanation, not evidence):

- 674 of 812 sessions are failed attempts, and a session's edits are mostly a subset of its own task's merged fix
  (513 of 674 failed, 119 of 138 resolved). So a session's file list is a noisier copy of a fix.
- A gold file is named by some earlier fix for 266 of 300 test queries. It is named only by earlier sessions, never
  by an earlier fix, for 8 of 300 (2.7%), and 11 of 297 on the development half. So new file information from
  sessions can lift file hit@5 by at most about 3 points on this data.
- On the development half (297 queries, the half `vote_prior` was tuned on): `vote_prior` 64.0%, `issue_fix_vote`
  59.3%. Searching sessions but voting with those tasks' merged fixes gives 65.0%. Searching past fixes by issue text
  and fix file paths, with no session, gives 63.3%. Most of v2's gain is file-path words in the search index, which a
  fix record has too. What is left for sessions is the agent's own text (commands, code names, error output) as a
  search index, about +1.7 points on the development half.

The sessions are single SWE-agent runs on one issue each. They hold no architectural decisions or cross-task notes, so
v3 tests only what they do hold: the agent's exploration as a way to find related past work.

## Rankers (`recallbench/unseen.py`), frozen

All fuse with the past-fix prior using v2's frozen `VOTE` settings. Nothing is tuned for v3.
- `session_index_fix` (candidate): earlier tasks in the order their first session appears in v1's `hybrid` ranking
  (BM25 on issue plus agent turns, fused with BM25 on session file paths). Each votes with its merged fix's files.
- `fix_hybrid` (strongest no-session control): earlier tasks by reciprocal rank fusion (v1's `RRF_K`) of BM25 on
  their issue text and BM25 on their fix's file paths, newer first on ties. Each votes with its merged fix's files.
- `vote_prior`, `issue_fix_vote`, `past_fix_files`: v2, unchanged.

## Evaluation data

Set A (same eight repos, never seen): the rows of the pinned `nebius/SWE-bench-extra` revision in `MANIFEST.json`
(same file, same sha256) whose repo is one of the eight and whose task has no trajectory, so it is not in
`data/tasks.jsonl` and its gold files were never read. Records are built by `tools/fetch_data.py`'s `task_record`
(same redaction). Tasks with no changed file are dropped.
- Corpus: the 677 known tasks plus set A. A query's fix history is every same-repo task created strictly before it,
  known or unseen, as a repo's merged fixes would be. Sessions are the pinned 812, so session memory exists only for
  tasks an agent worked on.
- Queries: set A tasks with at least `MIN_HISTORY` (10) earlier same-repo tasks that have a session.

Set B (other repos, used only if set A has fewer than 150 queries): from the pinned trajectory revision, repos not in
the eight with 10 to 19 tasks that have a trajectory. Sessions by v1's rule (per task, the lowest-row resolved and the
lowest-row failed trajectory). Corpus: every SWE-bench-extra task of those repos. Queries: every task there with at
least 10 earlier same-repo tasks that have a session. A query's own sessions are never in its memory: memory and fix
history are strictly earlier by `created_at`.

Run order: count valid queries without scoring (`tools/unseen.py --count`), then score once. Under 100 queries: no
score. 100 to 149: scored and called underpowered. 150 or more: the confirmatory result.

## Metric and reading

File hit@5 (v2's main metric), with v2's paired query bootstrap and repository bootstrap (same seed and draws).
Primary comparison: `session_index_fix` minus `fix_hybrid`. Also reported: `vote_prior` minus `fix_hybrid`,
`vote_prior` minus `issue_fix_vote` (a replication of v2's headline), `fix_hybrid` minus `issue_fix_vote`.
A difference is clear only when both 95% intervals exclude zero. With about 300 queries a paired interval is roughly
plus or minus 3 points, so a gain near the development half's +1.7 is expected to read "no clear gain". That reading
is reported as it comes.

## Known limits

- Unseen tasks may differ from covered ones (a task may lack a trajectory because its environment failed).
- `created_at` is when an issue or PR opened. An earlier task's fix may have merged after the query opened, as in v1.
- The agent model may have seen later versions of these repos in training. This affects every session ranker equally.
