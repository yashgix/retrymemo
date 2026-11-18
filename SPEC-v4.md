# recallbench v4: a useful application of agent-session memory (pre-registration, in parts)

`SPEC.md`, `SPEC-v2.md` and `SPEC-v3.md` and their results stay unchanged. v4 is an extension. This file is written in
parts, each committed before the step it governs, and earlier parts never change.

## Part 1: data split (committed before any v4 outcome is looked at)

Data: every row of the pinned `nebius/SWE-agent-trajectories` revision in `MANIFEST.json` (80,036 attempts by
SWE-agent on SWE-bench-extra tasks), joined to the pinned SWE-bench-extra tasks. The unit of the split is the task
(`instance_id`), so all attempts on one task fall in one part.

- Tasks recallbench v1 to v3 used (all tasks of the eight repos and of the 23 set B repos) are always `train`.
- Every other task: `h = int(sha256("recallbench-v4:" + instance_id).hexdigest()[:8], 16) % 10`. 0 to 5 `train`,
  6 and 7 `dev`, 8 and 9 `test`.
- Opportunity checks and method design use `train` only. `dev` is used once to settle any setting left open, then
  the method is frozen with a tag. `test` is scored once after the tag.

## Part 2: opportunity, method and metrics (committed before `dev` is read)

Opportunity checks on `train` (2,390 tasks, 55,256 attempts; `tools/attempts.py` records):
- Known-failure check (flag a retry that repeats a failed patch): after an issue's first attempt fails, later
  attempts succeed 1% of the time, so "always predict failure" is already 99% right. Nothing for memory to add. Dropped.
- Recurring errors across tasks in a repo: the most common errors are malformed commands (`cd..`, `find.`), which a
  generic rule catches without memory, and errors from the agent's own reproduce scripts, which are often intended.
  Repo memory flagged error steps at 76% precision against 71% for a global no-memory rule, at lower recall. No clean
  label for a wasted call. Dropped.
- Retry handoff (chosen): an unsolved issue has no merged fix, so fix history cannot point at it. A failed attempt on
  the same issue has already explored the code. On `train`, 661 of 2,149 second attempts never opened a needed file.

Unit: the first retry of an issue, its second attempt (by row) after the first failed. One per task. Needed files are
the merged fix's files (`gold_files`).

Rankers:
- `handoff` (`recallbench/handoff.py`): files the failed first attempt opened (1 each) or edited (`edited_weight`
  each), ties by `fix_hybrid` rank, then `fix_hybrid`. Files the attempt made itself with `create` are left out.
- `retry_agent` (the strongest no-memory baseline): the real second attempt, which had full code access and no memory.
  Its list is the repository files in the order it first opened them, without its own scratch files.
- `fix_hybrid`: v3's no-session fix index, from tasks created strictly before the issue.

Metrics: hit@1 and hit@5 (a needed file in the first 1 or 5). Primary comparison: `handoff` minus `retry_agent` on
hit@1. Also reported: hit@5, `handoff` minus `fix_hybrid`, the two disagreement counts at hit@1, `handoff` hit@1
on the retries that never opened a needed file, and the retries' median step of first opening a needed file.
Uncertainty: v2's paired bootstrap over tasks and over repositories. Clear only when both 95% intervals exclude zero.

What this does and does not show: it measures whether the handoff points to a needed file more often than the next
attempt finds one on its own. It does not run an agent with the handoff, so it does not show that the retry would then
resolve the issue or by how many steps it would be faster.

Open setting: `edited_weight` in {1, 2, 4}, chosen by `handoff` hit@1 on `dev` (ties go to 2). Then tag
`v4-method-frozen` and score `test` once.

## Part 3: setting chosen on `dev`, method frozen (committed and tagged before `test` is read)

On `dev` (433 first retries), `handoff` hit@1 is 70.4% for `edited_weight` 1, 2 and 4 alike: an edited file is
always opened too, so any positive weight puts edited files first in the same order. The tie goes to 2. `test` is
scored once with `tools/handoff.py --split test --edited-weight 2`, and the output is committed as
`results/handoff.json`.
