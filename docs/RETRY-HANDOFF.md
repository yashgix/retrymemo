# v4: a retry handoff from a failed attempt

Pre-registration: `SPEC-v4.md` (three parts, each committed before the step it governs), tag `v4-method-frozen`.
Result: `results/handoff.json`, one run of `tools/handoff.py --split test --edited-weight 2` on the records
`tools/attempts.py` writes from the pinned trajectory files (about 1.1 GB, so CI runs only the tests).

## The idea
v3 found that memory of other tasks' sessions adds nothing over a well-indexed history of merged fixes. An unsolved
issue is different: it has no merged fix yet, so fix history cannot point at it. When an attempt on it fails, the next
attempt can get a handoff: the files the failed attempt opened or edited (its own scratch scripts removed), then the
v3 fix index.

## Pre-registered result (test, 442 first retries over 361 repos, scored once)
A hit means a file the merged fix changed is first (hit@1) or in the first 5 (hit@5).

| Method | hit@1 | hit@5 |
|---|---|---|
| Retry handoff | 69.0% [64.9, 73.3] | 84.6% |
| Real second attempt: the files it opened, in order (no memory, full code access) | 60.0% [55.2, 64.5] | 74.7% |
| v3 fix index (no session) | 25.1% | 41.4% |

Handoff minus the real retry at hit@1: +9.0, 95% intervals [+4.8, +14.3] over tasks and [+4.4, +13.9] over repos.
hit@5: +10.0. Disagreements at hit@1: handoff right and retry wrong 82 times, the reverse 42 times. 16 of the 442
retries resolved their issue.

## What the +9.0 is made of (checks after the result, not pre-registered)
- 56 retries (13%) opened no repo file and made no patch. They score 0 by construction, while the handoff falls back
  to the fix index. Leaving them out: 70.5% against 68.7%, +1.8 [-3.1, +6.5].
- A fairer no-memory baseline gives the retry the same fallback (its opened files, then the fix index): 63.3%. The
  handoff gain over it is +5.7 [+1.4, +10.6] over tasks and [+1.3, +10.3] over repos.
- Applying the handoff rule to the retry's own trajectory (what the retry knows at its end) gives 71.5%, and the
  handoff minus that is -2.5 [-7.2, +2.0]. The failed attempt's opened files alone score 58.6%, against 60.0% for
  the retry's.

Read plainly: a failed attempt holds about what the next attempt finds by itself. The handoff gives that at the
first step, where the retry reached a needed file at median step 5 (median 18 steps per retry), and it covers for
retries that stall. It does not hold information the next attempt could not find. Whether starting at the right file
makes a retry solve more often, or cost less, needs agent runs and is not measured here.

## Corrections and notes
- SPEC-v4 Part 3 gives the wrong reason for the dev tie. Edited files are not always opened: 8,031 of 72,056
  edited paths never appear in `first_open` (edits made through shell commands, for example). The tie across weights
  1, 2 and 4 is real. Weights 2 and 4 give the same order, and weight 1 differs only on edited files that were
  never opened.
- SPEC-v4 Part 1 lists 23 set B repos as always `train`. v3 loaded sessions for six more (asottile/setup-cfg-fmt,
  networkx/networkx, stfc/fparser, matchms/matchms, canonical/operator, sanic-org/sanic), none with a valid v3 query,
  so no v3 outcome was read from them. 15 of their tasks are in `test`.
- `retry_first_needed_step_median` counts a needed file the retry created with `create`, while the `retry_agent`
  list does not. This affects only that descriptive number.
- The `create` filter compares the typed path with the opened path, so 61 scratch files in test (such as
  `pkg/reproduce.py` typed as `reproduce.py`) pass through on both sides.
- The attempts are independent samples, not retries in time. First and second are by row order.
