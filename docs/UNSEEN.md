# v3 on unseen tasks: reading the result

Pre-registration: `SPEC-v3.md`, tag `v3-method-frozen`. Result: `results/unseen.json`, from one run of
`tools/unseen.py --source DIR --expand` (needs the pinned parquet files, about 1.1 GB, so CI runs the tests only).

## What was scored
- Set A: 39 tasks from the eight repos that have no trajectory, so they were never in `data/tasks.jsonl`.
- Set B: 132 tasks from 23 other repos (10 to 19 tasks with a trajectory each), with 447 sessions picked by v1's rule.
- The confirmatory set is the pool of both, 171 tasks over 27 repos. SPEC-v3 adds set B to set A when A has fewer
  than 150 queries. The A-only and B-only rows below are descriptive (A is under the floor of 100, B is in the
  underpowered band).
- Leakage check on all 171: every session in memory and every fix in history comes from a strictly earlier
  same-repo task, and none is the query itself.

## File hit@5
| Ranker | A (39) | B (132) | All (171) |
|---|---|---|---|
| fix_hybrid (no sessions) | 89.7 | 70.5 | 74.9 |
| past_fix_files (no sessions, no search) | 92.3 | 66.7 | 72.5 |
| issue_fix_vote | 89.7 | 65.9 | 71.3 |
| session_index_fix | 82.1 | 66.7 | 70.2 |
| vote_prior | 82.1 | 57.6 | 63.2 |

Pooled differences with 95% query and repo intervals:
- session_index_fix minus fix_hybrid: -4.7 [-8.8, -1.2] [-8.1, -1.4]
- vote_prior minus fix_hybrid: -11.7 [-17.5, -5.8] [-20.4, -3.1]
- vote_prior minus issue_fix_vote: -8.2 [-14.6, -1.8] [-17.9, +2.3] (v2's +3.0 does not replicate)
- fix_hybrid minus issue_fix_vote: +3.5 [+1.2, +6.4] [+1.0, +7.5]

## What the -4.7 measures
Session memory exists only for tasks an agent worked on: for the median set B query, 11 of 20 earlier tasks. Fix
history covers all of them. So the pre-registered gap mixes how much history each ranker sees with how good the
signal is. A post hoc check (not part of the pre-registration) limits `fix_hybrid` to tasks that have a session: 71.9
pooled against `session_index_fix` 70.2, a gap of -1.7, and 66.7 each on set B. Read plainly:

- Where sessions and fixes cover the same tasks, sessions as a search index are no better than issue text plus fix
  file paths.
- Sessions as a source of files (`vote_prior`) are clearly worse. Most sessions are failed attempts, and their file
  lists are noisier than merged fixes.
- In a real repo, sessions cover only some of the work, while merged fixes cover all of it. That coverage gap is the
  larger part of the -4.7.
- Even the plain list of the files earlier fixes changed most (72.5) beats both session rankers.

## Disclosures
- The set B loader (`other_repo_sessions`, `--expand`) was written after the tag and after the set A count was seen.
  The rule was fixed in SPEC-v3 before both. It reuses `tools/fetch_data.py`'s `select()` with bounds, and a
  rerun after that refactor reproduced `results/unseen.json` byte for byte.
- Set B repos are small, with 1 to 17 queries each. Tasks without a trajectory may differ from the others (for
  example, an environment that failed to build).
- `created_at` is when an issue or PR opened, so an earlier fix may have merged after the query opened, as in v1.
- The sessions are single SWE-agent runs on one issue. They hold no design notes or cross-task handoffs, so this says
  nothing about those kinds of memory.
