# Replay memory experiment: pre-registered analysis

Frozen before any paid run (17 Nov 2026). `replay/FROZEN.json` holds the hash of every input listed here, and
`python3 -m replay.analyze` refuses to run if one changed. The commit and tag are recorded in the author's run notes.
This is an exploratory case study on five tasks, not evidence for a broad claim.

## Design
- Tasks: lock, verifier, simlink, onnx, fczero (`replay/tasks/*/task.json`, tests, patches, inputs).
- Conditions: none, instructions, retrieved, oracle, random (`replay/memory/<task>/<arm>-<seed>.md`).
- Seeds 1 to 5, one run per task × condition × seed: 125 runs. Same model (DeepSeek `deepseek-flash`), agent
  (mini-swe-agent 2.4.6), prompt template, step limit 60, run cost limit $0.08, wall limit 30 minutes.
- Runs are ordered seed by seed, so a stop at the cap leaves whole seeds.

## Outcomes per run (from the withheld tests only, no judge)
- **avoided** (primary): the task tests, every regression test and the memory tests passed.
- **pass** (task success): the task tests and every regression test passed.
- tokens (input, cached input, output), steps, seconds, cost (peak prices from the API's usage data).

## Which runs count
- **Missing data, never scored as a fail:** a run that crashed (only the harness, the API or Docker raise a crash,
  for example an outage, a failed first call or the global cap; the agent's own endings are a submit or a limit), or
  a run whose scoring failed (`scoring_error` is set). Each is counted and listed. Its cost still counts.
- **The runner stops after a crash.** Rerunning the same command retries each missing cell once, never more.
- **Every other run counts**, whatever its ending (submitted, the step, cost or time limit, the model's context
  limit, format errors). Its working tree is scored like any other.
- Before each run the runner refuses a payload with private text or a leak of the task's answer (a crash, so missing).
- **Complete seeds:** a seed is complete when all 25 task × condition cells have a counted run. The primary analysis
  uses complete seeds only. A sensitivity analysis uses every counted run. With fewer than 3 complete seeds the
  primary reading is "too little data", whatever the numbers.

## Task groups
- **Primary set:** lock, verifier, onnx, fczero.
- **simlink is reported separately.** Its lesson (symlinks) is the task's own requirement, so pass and avoided are the
  same outcome there, and it is never pooled with the others.
- **Is the lesson already in the repo at the task's start?** Rule: the repo already applies the lesson's pattern
  somewhere. lock (a comment in `src/feedback/apply.ts` says to use `FOR NO KEY UPDATE`), verifier (a lock query
  inside `try` with `client.release()` in `finally` in `src/jobs/rate-limit.ts`) and onnx (`src/match/model.ts`
  already validates the logistic model's full shape) do. fczero and simlink do not: there the memory adds
  information the repo lacks. The primary comparisons are also reported for lock, verifier and onnx against fczero
  alone (descriptive only).

## Primary comparisons (outcome: avoided, primary set, complete seeds)
1. oracle vs none
2. retrieved vs none
3. oracle vs random

For each: the rate per task (share of its seeds), the difference per task, and the mean of the four differences.
- **Test:** exact two-sided sign-flip permutation test over tasks (all 2^4 = 16 sign patterns of the per-task
  differences, p = share with an absolute mean at least as large). Holm adjustment across the three comparisons.
  With four tasks the smallest possible p is 0.125, so no comparison can be significant at 0.05. That is expected.
- **Interval:** 95% percentile bootstrap of the mean difference, 10,000 resamples with random seed 20261117,
  resampling tasks with replacement and then each arm's seeds within a task with replacement.
- **Per-task rates:** Wilson 95% intervals.
- **Reading, fixed in advance:** "consistent benefit" needs a positive difference in at least 3 of the 4 tasks and a
  bootstrap interval above 0. "Consistent harm" is the mirror. Anything else is "no clear effect".

## Secondary results (same method, reported, not tested further)
- The three comparisons on pass (task success), and instructions vs none on both outcomes.
- simlink: rates per condition and the same differences.
- Retrieval: for each task, where BM25 ranks the hand-picked lessons and how many the retrieved text holds.
- Tokens, steps, seconds and cost per condition (mean and median), overall and per task, the total cost, and the
  number of calls whose usage data was missing.
- Cost check: the summed run costs, the runner's `spend.json`, and the DeepSeek balance before and after each
  invocation (`balance-*.json`; it moves in cent steps and late).
- The one smoke-test request before the grid counts toward the $10 but is not part of the analysis.
- The sensitivity analysis on every counted run, and the list of missing runs with their reasons.
