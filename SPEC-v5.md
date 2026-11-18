# recallbench v5: does the retry handoff help a real coding agent? (DRAFT, not frozen)

**Status: frozen at the tag `v5-method-frozen` (16 Nov 2026, approved by the owner). The main run needs a separate approval.** `SPEC.md` to `SPEC-v4.md`, their tags and their results stay unchanged. v5 is an extension.

## Why v5

v4 showed, offline, that a file list built from a failed attempt points to a needed file more often than the next
attempt finds one on its own (hit@1 69.0% vs 60.0% on 442 test retries). It did not run an agent, so it could not say
whether a retry that gets the list solves the issue more often or faster. v5 runs one.

## What the pilot showed (train only, `results/v5-pilot.json`)

10 train first retries × 3 arms, DeepSeek Flash, with later git history removed (see below). This run cost $0.64 at
peak prices. Everything together (smoke test and the two runs set aside included) cost $1.21 counted and $1.15 by
the balance.
- An earlier run of the same 30 was set aside: the images kept the upstream git history, and 29 of 30 runs searched it
  (`git log --all`), some finding the task's own merged fix. With that history removed, no run saw its task's PR.
- Solved: A 10/10, B 9/10, C 9/10. The dataset's own 2024 SWE-agent retries had failed all 10. So on tasks of this
  kind, solve rate is near a ceiling and has little room to move. Both failures applied cleanly and failed the test.
- Steps per run, median A 19.5, B 16, C 18.5. Paired B−A: mean −1.4 (SD 3.5, fewer steps on 6 tasks, more on 2).
  C−A: mean −0.1 (SD 4.9).
- Cost per run is about $0.02. In total B cost 4% more than A, C 20% more (longer first message, more output).
- First step that names a needed file: median 2 in A, 1 in B, 1.5 in C. The agent finds the file by itself almost at
  once, so the handoff's head start is about one step.
- No run was "misled" (final patch edits the handoff's wrong first file). No crash, every run submitted.
- One of 11 tasks failed image validation (its tests did not run even with the real fix) and was dropped.

## Decisions (16 Nov 2026)

1. Steps per run and cost per run are the primary measures. Solve rate is reported, not the headline.
2. Each task × arm runs 2 times.
3. Arm C is dropped from the main run. Its pilot numbers stay reported as they are.
4. The main run does not start yet. First, a check of whether the tasks can give a useful test (below).

## Pool check: can these tasks test the handoff? (`agent/check_pool.py`, `results/v5-pool.json`)

Task metadata only, no agent runs. On dev: image, issue text and the real fix. The handoff and the 2024 retry are
looked at on train only.
- Pool: 159 of 433 dev first retries have an image, from 145 repositories (so the repository bootstrap is close to a
  task bootstrap). 226 GB to download, median 1.36 GB, 2 images over 2 GB.
- The image tasks are the easier part of dev: median real fix 10 changed lines, against 22.5 for dev tasks without an
  image. 53% of fixes touch more than one file. The pilot's tasks were similar (median 9 lines).
- Room for the handoff: 72% of the image tasks have an issue that names none of the fix's files (path, file name or
  dotted module). On train, those are where the 2024 retry got lost more often (never opened a needed file: 36% vs 16%)
  and where the handoff still points to one (hit@1 58% vs 80%). So most tasks leave the handoff something to do.
- But the 2026 agent searches well. In the pilot it reached a needed file by step 2 (median) without help, and more than
  half of B's saved steps came from the 2 tasks where A needed 3 steps to get there (6 saved on each). The issue
  naming a file did not predict that in 10 tasks. So the expected gain is about 1 step of about 20 (about 5%).
- Power, from the pilot spread (paired SD 3.5 steps and $0.003, one run each): with 150 tasks the smallest
  difference seen 80% of the time is about 0.8 steps (4%) and 3.5% of cost. Two repeats can only make this smaller.
  A 1.4-step effect (the pilot's) would be found almost surely, a 0.7-step effect about 70 to 90% of the time.
- Solve rate: not testable here (pilot 10/10 without the handoff, easy pool).
- Verdict: the A/B run can give a clear answer on steps and cost, most likely "a small saving in steps (around 5%),
  no saving in cost", or "no difference". It cannot show a solve-rate gain.

## Tasks (fixed before the main run)

- Unit: the first retry of an issue (v4's definition), on the v4 `dev` split: its 433 first retries, of which 159 have
  a SWE-rebench image (checked on Docker Hub, 15 Nov 2026). The v4 `test` split is never used.
- Image validation before any agent run, at no model cost: with the test patch only, at least one FAIL_TO_PASS test
  fails; with the real fix, every FAIL_TO_PASS test passes. PASS_TO_PASS tests that fail even with the real fix are
  dropped for that task. Tasks that fail validation are dropped and listed.
- Task files (`agent/prepare_pilot.py`, sha256 order of a fixed prefix + id, built before the tag; the prefix the published pilot used is private, see `PILOT_ORDER_PREFIX`). The SWE-rebench
  rows come from its parquet export (16 Nov 2026, read by `agent/rebench_rows.py`), because the rows API was down.
  Rebuilt from these rows, the pilot's task file came out identical.
  - `tasks-dev.json`: dev first retries whose image is on Docker Hub (at most 3 GB), whose SWE-rebench row names that
    image, and whose FAIL_TO_PASS is not empty. 138 tasks. Of the 159 with an image, 17 have no SWE-rebench row and 4
    have a row with no image. sha256 `1fc90bc1bda217cc06bed046239cfa7d3baad7a47ee9f31f8391b3a8c1e13bd4`.
  - `tasks-train-fill.json`: 50 train first retries, same order and pilot rules (image at most 2 GB), leaving out the
    13 tasks of the pilot's task file (11 ran, 2 were never reached). sha256
    `648a1baecf620820757cea2c00ebc3df4436630244f5b2ee18ce0a3e96a104db`.
- The runner takes dev first, then the fill, and stops after 150 tasks that passed validation and had no crashed run.
  So about 15 to 25 train tasks are expected in the 150, reported apart as well as in the total.
- A pre-registered subgroup: tasks where the 2024 retry never opened a needed file (v4's "stalled retries"), recorded
  in the task files from the data before any run.

## Agent and settings (frozen at the tag)

- `agent/run_pilot.py` with `--arms A,B --repeats 2` (the defaults): per task, the 4 runs go in parallel. Container,
  history removal and scoring code are the pilot's, unchanged.
- mini-swe-agent 2.4.6, its `benchmarks/swebench.yaml` system and instance templates unchanged, tool-call mode.
- Model `deepseek-flash` (DeepSeek-V4.1-Flash) through DeepSeek's OpenAI-compatible API (`agent/deepseek_model.py`),
  default settings (thinking on, default temperature), `parallel_tool_calls` on.
- Limits per run: 100 steps, $0.40, 45 minutes. Command timeout 180 s.
- Each run in its own container from the task image, `--network none`, conda `testbed` env on PATH. The API key stays
  on the Mac.
- Before the agent starts, the container's git history after the base commit is removed (every ref but the base
  branch, remotes, reflog, then unreachable objects pruned) and checked: every stored object must be reachable from
  the base commit. A run whose check fails does not start.
- The submitted patch is read as bytes with `git diff` from the agent's container, for the files the agent submitted.
- A crashed run (an error outside the agent, such as an API outage) stops the whole run, so an outage cannot use up
  the tasks. The crashed task is left out and listed, and the run resumes with the next task.

## Commands (from the repo root, task files in `../v5-runs/v5-main/`)

```
../v5-env/bin/python -m agent.run_pilot --tasks ../v5-runs/v5-main/tasks-dev.json \
  ../v5-runs/v5-main/tasks-train-fill.json --out ../v5-runs/v5-main --count 150 --cap 15 --prior-spend 0
python -m agent.analyze_v5 --results ../v5-runs/v5-main/results.jsonl --tasks ../v5-runs/v5-main/tasks-dev.json \
  ../v5-runs/v5-main/tasks-train-fill.json --out results/v5-main.json
```

## Budget and stops

- Hard cap $15 of main-run spend, counted at peak prices (`--cap 15 --prior-spend 0`; pilot spend is not counted).
- A task starts only if spend so far plus $0.50 × 4 runs still fits under the cap, so starts stop near $13. At the
  pilot's rate 150 tasks cost about $12. If the cap stops the run first, the tasks done so far are analyzed and the
  shortfall is stated.
- mini-swe-agent's own global cost limit is set to the same cap as a second stop.

## Arms (`agent/handoff_text.py`, frozen at the tag)

The text is added after the issue in the agent's first message, inside `<previous_attempt>` tags.
- A: nothing.
- B: v4's frozen file list (`recallbench/handoff.py`, `edited_weight` 2), top 5, with the line that an earlier attempt
  did not fix the issue and the files may not be the right ones. If the list is empty, it says so.
- (C, the list plus a note on the failed attempt, ran in the pilot only.)

## Measures

Per run: resolved (FAIL_TO_PASS all pass and kept PASS_TO_PASS all pass, in a fresh container after the agent's patch
and the test patch), steps (model calls), cost (peak prices), input and output tokens, wall time, first step whose
command names a needed file, "misled" (final patch edits B's first file and that file is not a needed one), exit status.

## Analysis (pre-registered)

- `agent/analyze_v5.py`. Tasks with a crashed or missing run are left out and listed.
- Per task, average steps, cost and resolved over its repeats. Difference B−A, paired by task, with intervals. Tokens,
  wall time, first needed step and misled are reported per arm (medians and counts), without intervals.
- Intervals: v2's paired percentile bootstrap over tasks and over repositories (`recallbench/stats.py`, seed
  20261114, 1,000 draws). An effect is clear only when both 95% intervals exclude zero.
- Solve rate is reported with an exact McNemar test on runs paired by task and repeat, where the arms disagree.
- The stalled subgroup is reported the same way, marked as a subgroup.
- The dev tasks alone (without the train fill) are reported the same way.
- With 150 tasks a difference of about 0.8 steps (4%) or 3.5% of cost is detectable (pool check above).

## Limits to state

- The failed attempt is a 2024 SWE-agent run (mostly Llama-70B, some Llama-8B). The retry is a stronger 2026 model
  on the same old public issues, which it may have seen in training. This affects all arms alike (paired design), and
  it is the likely reason for the ceiling.
- Python repos with SWE-rebench images only. x86 images under emulation on an arm Mac (no failures seen in the pilot).
- "Needed files" come from the merged fix. Steps count model calls, and one call can run several commands.
- The McNemar test treats a task's two repeat pairs as independent, so its p-value is too small. It is secondary.

## Pilot changes made before this draft (plumbing, not tuning on outcomes)

litellm's tool-call bug bypassed; peak-price cost; conda `testbed` env; new files left out of arm C's patch; CRs kept
in submitted patches (this turned a correct fix into a fail on the first task, so that run was set aside); empty B
list worded; later git history removed (the full run before it was set aside); each arm reserves $0.50 against the
cap, mini-swe-agent's global cost limit as a second stop, and costs of crashed runs counted on resume.
