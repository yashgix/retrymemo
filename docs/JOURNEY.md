# Engineering log: from file retrieval to agent behaviour (14 to 18 Nov 2026)

This is the chronological record of every experiment in this repository, including the negative results, the
approaches that were considered and dropped, and what each independent review changed. Numbers come from the JSON
files in `results/`, and each section names its source. A few historical details, marked †, come from the author's
private run notes rather than files in this repository. Times are US Eastern.

Reviews mentioned below are of three kinds: an independent reviewer session (a separate, read-only model session
given the code and data, not the author's conclusions), an independent second-model review run read-only, and a second
assistant reviewing the written reports. None of them could change files.

## Contents

1. [v1: which past sessions point to the right files?](#v1-which-past-sessions-point-to-the-right-files)
2. [v2: a fair metric, baselines and a held-out half](#v2-a-fair-metric-baselines-and-a-held-out-half)
3. [v3: does any session signal beat a fix index on unseen tasks?](#v3-does-any-session-signal-beat-a-fix-index-on-unseen-tasks)
4. [v4: memory of a failed attempt on the same issue](#v4-memory-of-a-failed-attempt-on-the-same-issue)
5. [v5: does the handoff help a real agent?](#v5-does-the-handoff-help-a-real-agent)
6. [Choosing the next direction](#choosing-the-next-direction)
7. [The replay pilot](#the-replay-pilot)
8. [The 125-run memory replay](#the-125-run-memory-replay)
9. [Why we stopped](#why-we-stopped)
10. [Known inconsistencies in the records](#known-inconsistencies-in-the-records)

| Date | Step | Tag or commit |
|---|---|---|
| 15 Nov | v1 built and accepted | `v1-pipeline-final` |
| 15 Nov | v2 settings frozen, test half scored once | `v2-settings-frozen` |
| 15 Nov | v3 method frozen, 171 unseen queries scored once | `v3-method-frozen` |
| 15 Nov | v4 method frozen, 442 test retries scored once | `v4-method-frozen` |
| 16 Nov | v5 method frozen, 576 agent runs | `v5-method-frozen` |
| 17 Nov | replay pilot (10 tasks, $0), then five tasks prepared | `dc52d42` |
| 17 Nov | replay analysis pre-registered, one live smoke request | `replay-analysis-frozen` to `-3` |
| 17-18 Nov | privacy repair, then freezes 4 to 14 after review rounds | `replay-analysis-frozen-4` to `-14` |
| 18 Nov | 125 runs complete, frozen analysis, results review | `1e55684` |

---

## v1: which past sessions point to the right files?

**Problem.** A team whose agents share memory builds up many past sessions. A memory layer has to choose a few to put
in front of the next task. Retrieval is not the whole product, but it is a precondition: if the chosen sessions point
at the wrong code, the agent gets noise.

**Approach.** Measure that recall step alone, on public data, with an objective label and no model calls, so every run
is cheap and repeatable.

**Built.** The `recallbench` package: data loading, a BM25 scorer, seven rankers (recency, BM25 on the past issue, on
the full session, on file paths, a reciprocal-rank fusion called `hybrid`, a resolved-only filter and a staleness
re-ranker), metrics, a token-budget packer and a paired bootstrap. `tools/fetch_data.py` rebuilds the data from pinned
Hugging Face files with sha256 checks and pattern-based redaction.

**Data and method.** 677 SWE-bench-extra tasks and 812 SWE-agent sessions in 8 Python repositories, 597 queries. The
pool for a task is the sessions of the same repository's tasks created strictly earlier (by pull-request creation
date, not by when a session ran or a fix merged). A session is relevant if it names a file the
task's real merged fix changed. Metrics: session hit@1 and hit@5, recall@5, nDCG@5, and hit rate under token budgets.

**Result** (`results/metrics.json`). `hybrid` 64.2% session hit@5 [60.5, 68.0] against 48.9% for BM25 on the issue.
Short four-line cards beat full sessions under small budgets.

**What review found.** An independent assessment showed the headline was not defensible. A ranking that ignores the
query and simply prefers sessions naming many files scored 63.0%, within noise of `hybrid`, because session hit@5
rewards naming more files. The card advantage was true by construction (more cards fit in a budget). Lint failed and
there was no CI.

**Learned.** The metric has to hold every ranking to the same number of files, query-blind baselines are required, and
choices need a held-out half. That became v2. v1's specification and tests were kept frozen.

## v2: a fair metric, baselines and a held-out half

**Problem.** Once the metric confound is removed, does session memory still beat simpler signals?

**Built.** `SPEC-v2.md`, a file-level metric (first 5 distinct files, edited files first), two baselines that any
retriever should beat (`most_files`, query-blind, and `past_fix_files`, the files earlier fixes changed most often,
with no session at all), a per-repository date split, a repository-level bootstrap, a 400-setting tuning grid and the
retriever `vote_prior` (top `hybrid` sessions vote for files, fused with the fix-history prior). CI was added.

**Method.** Each repository's earlier half of queries is development (297), the later half is test (300). Settings
were frozen at `v2-settings-frozen` before the test half was scored once. A difference counts as clear only when both
the query and the repository intervals exclude zero.

**Result** (`results/files.json`, test half, file hit@5). `vote_prior` 52.7%, `hybrid` 43.0%, `past_fix_files`
40.0%, `bm25_issue` 39.7%, `most_files` 21.0%. `vote_prior` beat `hybrid` clearly (+9.7) and the prior over queries
(+12.7, but not over repositories).

**What review found.** The second-model review asked for a stronger control: let the fixes of similar earlier issues vote, with no
sessions. That control, `issue_fix_vote`, was added after the test half had been scored and run with the frozen
settings. It reached 49.7%. `vote_prior` minus the control was +3.0 [-2.7, +8.0], not a clear difference, and the
two had the same mean over repositories (65.0% and 65.3%). The `vote_prior` idea itself came from a look at all
queries before the split, which is disclosed.

**Learned.** On this data, session memory adds little beyond a query-aware use of fix history. The obvious next
question was why.

## v3: does any session signal beat a fix index on unseen tasks?

**Problem.** Is there any session-specific signal that beats a strong fix index on tasks the project never saw?

**Approach.** Diagnose on existing data first, freeze one candidate and its strongest control with a tag, and only then
score genuinely unseen tasks once. Rule fixed in advance: at least 150 unseen queries for a confirmatory reading.

**Diagnosis** (development data, post hoc). 674 of 812 sessions failed, and their edits were mostly a subset of the
real fix. A gold file was named only by sessions, and by no earlier fix, for 8 of 300 test queries (2.7%).

**Built.** `SPEC-v3.md`, `recallbench/unseen.py`, `tools/unseen.py`. The candidate `session_index_fix` (sessions as an
index, fixes as voters) against `fix_hybrid` (the strongest no-session control).

**Data.** Set A, 39 queries from the same 8 repositories with tasks that had no trajectory. Set B, 132 queries over 23
other repositories from the pinned 1.1 GB trajectory files. 171 pooled queries over 27 repositories. A leakage check
found 0 violations.

**Result** (`results/unseen.json`, file hit@5). `fix_hybrid` 74.9%, `past_fix_files` 72.5%, `issue_fix_vote` 71.3%,
`session_index_fix` 70.2%, `vote_prior` 63.2%. The candidate lost to its control by -4.7 [-8.8, -1.2] (repositories
[-8.1, -1.4]). v2's +3.0 for `vote_prior` over `issue_fix_vote` did not replicate (-8.2 [-14.6, -1.8]). About 3
points of the gap are a coverage effect (the candidate only ranks tasks that have sessions), measured post hoc.

**What review found.** The reviewer confirmed no leakage and that the code matched the spec, and asked for clearer
reporting: state the coverage confound, which set is confirmatory, and that the set B loader was written after the
tag (the rule for it was fixed before). All applied, rerun byte-identical.

**Learned.** Across tasks, merged fixes are the better memory. Single failed sessions add no reliable file signal. So
the next idea had to use memory where fix history cannot help.

## v4: memory of a failed attempt on the same issue

**Problem.** An unsolved issue has no merged fix, so fix history is empty for it. But a failed attempt has already
explored the code. Can a handoff from that attempt point the next attempt to the right file?

**Approach.** One bounded attempt, with a task-level train, development and test split, the method frozen before the
test split was scored, and a negative result reported as it is.

**Opportunities checked on train first** (2,390 tasks, 55,256 attempts). A "known failure" warning was dropped
(retries succeed only 1% of the time, so there is no clean label). A recurring-error warning was dropped (76% against
71% precision, no clean label). The handoff was chosen: 661 of 2,149 second attempts never opened a file the fix
needed.

**Built.** `SPEC-v4.md` (pre-registered in three parts), `recallbench/handoff.py`, `tools/attempts.py`,
`tools/handoff.py`. The handoff ranks the files the failed attempt edited and opened, with the v3 fix index as the
fallback. The development split chose `edited_weight` 2.

**Data and metric.** All 80,036 pinned attempts, split by a hash of the task id (60/20/20), with the tasks of the
earlier benchmark's repositories always in train. The unit is the first retry after a failed first attempt. The
dataset's attempts are independent samples, so "first" and "retry" follow dataset row order, not real time. The primary metric is hit@1: is the first file a needed file (one the merged fix
changed)?

**Result** (`results/handoff.json`, test split, 442 retries in 361 repositories, scored once).

| Ranking | hit@1 | hit@5 |
|---|---:|---:|
| handoff | 69.0% (305/442) | 84.6% |
| real retry (what the next attempt actually opened first) | 60.0% (265/442) | 74.7% |
| fix index alone | 25.1% | 41.4% |

Handoff minus real retry, the pre-registered comparison: +9.0 [+4.8, +14.3] over tasks, [+4.4, +13.9] over
repositories.

**What review found.** The real retry sometimes opens nothing before it stalls (56 of 442, 13%), which scores 0 by
construction. Giving the real retry the same fallback as the handoff, a fairer baseline found by the independent
reviewer, gives 63.3% (280/442), and the handoff's lead shrinks to +5.7 [+1.4, +10.6]. That comparison is post hoc.
Without the stalled retries the lead is +1.8 [-3.1, +6.5]. The retry's own final set of opened files reaches 71.5%.
In other words, a failed attempt holds about what the next attempt finds by itself. Its value is a head start, and a
fallback when the retry stalls.

**This is file identification, not task completion.** No agent was run with the handoff, and the dataset's own
retries resolved only 16 of 442 issues.

**Learned.** The only way to know whether a head start matters is to run an agent with it. That became v5.

## v5: does the handoff help a real agent?

**Problem.** Does the v4 file list make a real coding agent solve more, or solve faster and cheaper?

**Feasibility first** ($0). 159 of 433 development retries had public SWE-rebench evaluation images. A power check
showed about 150 tasks could detect a change of about 0.8 steps (4%) or 3.5% in cost, but not a small solve-rate change.
Steps and cost were made the primary outcomes, solve rate reported.

**Built.** `agent/` (runner, a DeepSeek model wrapper, handoff text, pool check, analysis), `SPEC-v5.md`. Agent
mini-swe-agent 2.4.6 with its SWE-bench templates, model DeepSeek Flash (`deepseek-flash`), limits of 100 steps, $0.40
and 45 minutes per run, containers with no network, scoring by the task's FAIL_TO_PASS and kept PASS_TO_PASS tests in
a fresh container. Arm A no memory, arm B the v4 top-5 file list, 2 repeats each.

**The pilot caught a leak.** The first pilot run solved 30 of 30. The evaluation images still held the future fix in
their git history, and most runs had searched it (25 of 30 printed the real fix†). That run was discarded, and every
image's history after the base commit is now removed and checked before use.

**Result** (`results/v5-main.json`, frozen at `v5-method-frozen`). 188 tasks tried, 42 failed image validation (22%,
mostly environments where no test passes at all), 2 left out after the runs (one with API connection errors in every run, one incomplete), 144 tasks in 125
repositories analysed, 576 runs.

| Outcome | A: no memory | B: v4 file list | Difference |
|---|---:|---:|---|
| mean steps | 19.73 | 19.19 | -2.7%, [-1.43, +0.38] steps |
| cost | | | +4.4% [-1.4%, +9.8%] |
| solved | 86.1% | 87.5% | McNemar p 0.56 |

Nothing was clear. The agent found a needed file by step 2 on its own (median), against step 1 with the hint.

**What review found.** A second review accepted the null for this setup only and named its limits: a file hint only,
a fast and strong baseline (86% solved), the 42 excluded tasks and one agent configuration. It also corrected an
overclaim about the detectable effect size. The 42 image-validation exclusions were decided by the real fix before any
run, so they apply to both arms alike, but they limit how far the result generalizes. Two more were left out after the runs: one
where every run ended in API connection errors, and one left incomplete (the run notes† attribute these to a
network outage and a machine crash).

**Learned.** A 2026 agent locates files quickly by itself, so file hints add little. If memory is to matter, it has to
carry knowledge the agent cannot rediscover from the repository. Spend logged for the main run was $12.49 at peak prices
(`results/v5-main.json`), and at most $8.30 by the account balance†.

## Choosing the next direction

After v5, four parallel research passes looked at benchmark integrity, published agent-memory results, cheap ways to
improve agents and the startup landscape. Findings that shaped the choice, as recorded at the time in private notes†
(not re-checked for this write-up):

- Git-history leaks in agent benchmarks are already well known, so a leak audit would not be new.
- Published memory benchmarks report little gain for most memory setups, and large gains only when the needed
  knowledge cannot be inferred from the repository.
- Run-to-run noise of 2 to 6 points is normal for agent benchmarks.

| Option considered | Why it was not chosen |
|---|---|
| Audit public evaluation images for leaks and measure score inflation | Not new, and away from the memory question. Kept as a fallback |
| First sealed score for a cheap model on fresh tasks, with best-of-5 | Off the memory question. The cost claim shrank on checking |
| Memory against a blind retry at equal cost | Likely another null with low value |
| Memory mined from real pull-request review comments | Too slow for the time available |
| Run-to-run noise report | Already covered by two 2026 papers |
| Memorization probe | Already partly covered by published work |
| **Replay real review lessons from the author's own agent-built projects** | **Chosen.** Real repeated mistakes with dated review records, so memory can be cut strictly before each task |

None of the rejected options was built.

## The replay pilot

**Problem.** Can real project history make a fair memory benchmark: tasks that isolate cleanly, tests that tell a real
fix from a lucky one, and lessons a retriever can find?

**Approach.** A free 10-task pilot, before any spend. The counts in this section come from the pilot's notes†. 18 candidates were mined from 9 of the author's own repositories,
10 were rebuilt in disposable clones, withheld tests were run before and after, and BM25 retrieval was checked.

**Result.** Isolation held for all 10. The withheld tests failed before and passed after for all 10, but for 4 only
because a module or name was missing. Real first attempts had repeated the earlier lesson in 3 tasks. BM25 put a
useful lesson in its top 15 for 5 of 10 tasks, and cross-project lessons ranked 61st to 307th.

**What failed.** Shared `node_modules` caches held later code. Some memory tests passed on untouched code. Some tests
were white-box. And the pilot's own validation script linked the source repositories' `node_modules`, so the test
runner wrote ignored cache files into the original repositories (no tracked file changed). Those 341 cache files were
deleted, and a hard rule followed: experiments only ever run in real copies, sandboxed, with an unchanged check.

**Outcome.** REVISE. The protocol for a paid step: five memory conditions, test-only scoring with a joint "avoided"
rule, five seeds, five tasks, 125 runs, capped at $10.

## The 125-run memory replay

Full design, controls and results: [`REPLAY.md`](REPLAY.md). Pre-registration: [`../replay/ANALYSIS.md`](../replay/ANALYSIS.md).
Freeze history: [`../replay/REVISIONS.md`](../replay/REVISIONS.md).

**Preparation** ($0). Five TypeScript tasks built around mistakes that a reviewer had flagged in these projects: `lock`
(row locks), `verifier` (release a database client in `finally`), `simlink` (symlinks in a path check), `onnx`
(validate a model's full metadata) and `fczero` (never report a result from zero replays). For lock, verifier and onnx
the agent's real earlier attempt that made the mistake is kept as a control. For every task the untouched code fails
and the real fix passes and avoids the mistake. The other controls differ per task (see
[`REPLAY.md`](REPLAY.md#the-tasks)). Each check held 3 of 3 times. Two independent reviews found working-copy
text that should not leave the machine, which led to a redaction module and a neutral single-commit snapshot, and a
NO-GO on scoring and cap details that was fixed before GO.

**Pre-registration.** The analysis was frozen before any paid call, with hashes of every input. Then one bounded live
request proved the paid path ($0.0015, three cost records agreeing).

**First paid run, stopped by the privacy audit.** The first full run worked (valid grading, $0.047, records agree,
repositories unchanged), but a post-run audit found an internal branch name in `git log` output sent to the model. The
pre-send check had only covered the prompt. The repair put a fail-closed check on every outgoing request, the whole
conversation and tool output included, renamed the branch, and a probe of realistic git commands found three more
small exposures, which were redacted. That first run is excluded from the analysis and its cost stays in the ledger.

**Ten second-model review rounds before the grid.** Nine NO-GO verdicts, each fixed and refrozen before any counted run,
without touching the tasks, memory texts, conditions, seeds, tests or comparisons. Some rounds changed how crashed,
failed or cut-off runs are handled. What they caught was mostly about money and failure handling: spend
that could vanish on a crash before logging, two processes able to spend at once, a reachable total of about $10.02
through a balance floor, a stop that did not stop in-flight requests, and runs that hit the model's context limit
being scored as unchanged code (which would have penalized the long-memory conditions). GO at freeze 13, and a short
check of freeze 14, which only changed how the base image is looked up.

**The grid.** 115 runs completed before the Mac's disk filled. The harness stopped itself: one run crashed, and the
request boundary refused a request that carried Docker's error text with a local path, so nothing private was sent.
After a narrow cleanup of the experiment's own Docker images and build cache, and integrity checks (frozen inputs, image
IDs, packages, ledger chain, source repositories, balance), the last 12 cells ran. The two interrupted runs are kept as
diagnostic records and were retried once, as the plan says.

**Result.** All 125 counted runs passed their task. The three pre-registered comparisons all read "no clear effect".
See [`REPLAY.md`](REPLAY.md#results).

**Results review.** The second-model reviewer recomputed every primary number from the raw rows and they matched. It found no gaming of
the grading, and two reporting gaps in the frozen analysis: the secondary comparisons have no bootstrap intervals, and
token counts leave out 32 paid responses that followed format errors. Both are left as they are, because the analysis
is frozen, and are disclosed. Its recommendation was EXPAND.

## Why we stopped

The options after the replay were PUBLISH, EXPAND or STOP.

- **PUBLISH** a result was ruled out. Four primary tasks cannot carry a claim either way.
- **EXPAND** was the reviewer's pick: 6 to 8 new tasks where the lesson is absent from the repository, plus a distilled
  lesson condition next to raw records. That is a new experiment, with new preparation, new tasks and new spend.
- **STOP** was chosen. The work had answered what it could at this scale, and the honest record of it, positive and
  negative, is the deliverable.

## Known inconsistencies in the records

These were found while reconstructing the history. None changes a reported result.

- The discarded v5 pilot run is described in some notes as "29 of 30 runs searched git history" and in others as "25
  of 30 printed the real fix". They measure different things. Both are true of that run.
- The replay cost estimate cites "603 v5 runs", but the counted v5 runs add up to 606 (576 main, 30 pilot). The
  estimate's input set is not recorded.
- The paid grid first ran with `--cap 10` and the resume with `--cap 9.99`. Both sit inside the $10 approval, and the
  cumulative ledger counted every request from the first smoke call on.
- `onnx` and `verifier` were first judged "lesson not recoverable from the repo" in the pilot, and later classified "in
  repo" by the rule written into the frozen analysis. The frozen rule is what the results use.
- One approved redaction edited `fczero`'s own repository-hygiene test (a search pattern), although
  `replay/REVISIONS.md` says no test uses the redacted files. It does not touch the withheld grading.
- `SPEC-v5.md` still carries "DRAFT" in its title line, although it was frozen at `v5-method-frozen`.
- `replay-analysis.json` reports two hand-picked lessons in the onnx retrieved text. The second is cut off after its
  heading, so in effect one.
