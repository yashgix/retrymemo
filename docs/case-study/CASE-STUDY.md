# Does memory stop coding agents from repeating mistakes?

*A case study in testing agent memory honestly: six experiments, one clear offline retrieval gain, and no clear effect
on agent behaviour detected. Independent research, not affiliated with or endorsed by any company.*

The HTML version of this page, with charts, is [`index.html`](index.html).

## 1. The question

Teams that run coding agents build up history: past sessions, failed attempts, review comments, decisions. A shared
memory layer promises to feed the right piece of that history to the next agent so it does not repeat old mistakes.
The question here is narrow and testable: **when a coding agent is given the relevant lesson from earlier work, does
it avoid the mistake, and does memory beat simply reading the repository?**

## 2. The starting hypothesis

Memory would help in two steps. First, a good retriever would find past work that points to the right code. Second,
an agent given that pointer, or the lesson itself, would work faster and avoid known mistakes. The plan was to measure
step one cheaply and offline, then test step two with a real agent.

## 3. What was built

| Piece | What it does |
|---|---|
| An offline retrieval benchmark | 677 public SWE-bench-extra tasks and 812 public SWE-agent sessions in 8 repositories, a memory pool of tasks created strictly earlier (by pull-request date), BM25 and fusion rankers, file-level metrics and query and repository bootstraps |
| A retry handoff | turns a failed attempt on an issue into a ranked file list for the next attempt, evaluated on 442 held-out retries in 361 repositories |
| A real-agent harness | mini-swe-agent with DeepSeek Flash in network-less containers, scored by each task's own tests in a fresh container, 576 runs |
| A memory replay harness | five reviewer-flagged mistakes, five memory conditions, redacted snapshots, a fail-closed privacy check on every model request, a hash-chained spend ledger, and a frozen analysis that refuses changed inputs |

v1 was built first and then reviewed. From v2 on, every experiment was pre-registered and frozen with a git tag before
its held-out data was scored, and every step was checked by an independent reviewer that could read the code and data
but not change them.

## 4. The journey

| Step | Hypothesis | Result | Decision |
|---|---|---|---|
| v1 | past sessions point to the right files | 64.2% session hit@5, but a query-blind ranking scored 63.0% | the metric rewards naming more files, so fix the metric |
| v2 | with a fair metric, sessions beat fix history | 52.7% vs 49.7% for a no-session control, +3.0 [-2.7, +8.0] | no clear gain, so ask why |
| v3 | some session signal beats a strong fix index on unseen tasks | 70.2% vs 74.9%, -4.7 [-8.8, -1.2], 171 unseen queries | sessions are worse across tasks, so try memory where fix history cannot help |
| v4 | a failed attempt's memory guides the next attempt on the same issue | hit@1 69.0% vs 60.0%, +9.0 [+4.8, +14.3], 442 retries | a real file-identification gain, so test it with an agent |
| v5 | that head start makes a real agent better | steps -2.7% (interval includes 0), solved 86.1% vs 87.5% | the agent finds the file by step 2 anyway, so test lessons, not pointers |
| replay | remembered review lessons stop a reviewer-flagged mistake recurring | all 125 runs passed their task, no clear memory effect | stop |

## 5. The promising retrieval finding

The retry handoff is the one clear positive result. When an agent fails on an issue, the files it edited and opened,
with a fix-history fallback, rank a file the real fix changed first **69.0%** of the time, against **60.0%** for what the next attempt actually opened first. That is +9.0 points [+4.8, +14.3], the
pre-registered comparison on 442 held-out retries.

A fairer baseline, found by the independent reviewer, gives the retry the same fallback. It reaches 63.3%, and the
lead shrinks to +5.7 [+1.4, +10.6]. That comparison was made after the fact. Most of the gain comes from retries that
stall before opening anything. The honest reading: a failed attempt holds about what the next attempt would find
itself, so its value is a head start and a fallback.

This measures **file identification**, not task completion. No agent was run with it at that stage.

## 6. Why better retrieval was not enough

v5 put the handoff in front of a real agent: 144 tasks, two arms, two repeats, 576 runs. Steps went from 19.73 to
19.19 (-2.7%, interval includes zero), cost rose 4.4%, and the solve rate was 86.1% without the hint and 87.5% with
it. The agent found a needed file by step 2 on its own. The median step at which it first opened a needed file went from 2 to 1.

The pilot for v5 also caught a leak worth knowing about: the public evaluation images still held the future fix in
their git history, and the agent found it (25 of 30 runs printed the real fix, per the run notes). That run was thrown away and every image
is now cleaned and checked.

The lesson: if memory matters, it has to carry knowledge the agent cannot rediscover by reading the repository, such as
a decision a reviewer made or a mistake that only shows up later.

## 7. The controlled 125-run experiment

Five tasks built around mistakes a reviewer had flagged earlier, taken from the author's own agent-built TypeScript
projects. For three of them the agent's real earlier attempt that made the mistake is kept as a control. Five memory conditions:

- **none**
- **instructions**: one general build brief shared across projects
- **retrieved**: BM25 over the earlier review records, cut to 18,000 characters
- **oracle**: the two hand-picked lessons, padded with random records to the same size
- **random**: same size, no hand-picked record

Five seeds per cell, 125 runs, DeepSeek Flash with mini-swe-agent, at most 60 steps, $0.08 and 30 minutes per run.
Scoring is by tests only: **pass** means the task and regression tests pass. **avoided** means that, plus the
withheld memory tests, which check the specific earlier mistake. The analysis, its comparisons (oracle vs none,
retrieved vs none, oracle vs random) and its reading rules were frozen before the first paid request.

## 8. Results

All 125 runs passed their task. Runs that avoided the repeated mistake, out of 5:

| Task | None | Instructions | Retrieved | Oracle | Random | Lesson in repo? |
|---|---:|---:|---:|---:|---:|---|
| lock | 5 | 5 | 4 | 5 | 5 | yes |
| verifier | 5 | 5 | 5 | 5 | 5 | yes |
| onnx | 2 | 1 | 5 | 2 | 2 | yes |
| fczero | 0 | 0 | 0 | 0 | 0 | no |
| simlink (separate) | 5 | 5 | 5 | 5 | 5 | the lesson is the task |

| Comparison | Mean difference | 95% bootstrap | Reading |
|---|---:|---|---|
| oracle vs none | 0.00 | [-0.15, 0.15] | no clear effect |
| retrieved vs none | +0.10 | [-0.20, 0.45] | no clear effect |
| oracle vs random | 0.00 | [-0.15, 0.15] | no clear effect |

Retrieval put the useful lesson into the memory text for **one task of five** (onnx). The instructions, oracle and random texts cost 15 to 18%
more per run than no memory, retrieved about 1% more. The whole replay cost **$4.53** of a $10 cap, smoke test and an excluded first run
included.

## 9. What failed, and why

- **Ceiling.** On lock, verifier and simlink the agent avoided the mistake without memory. The repository already
  showed the right pattern, or the task asked for it.
- **Floor.** On fczero no run fixed the problem, not even with the hand-picked lesson. The lesson was one line inside
  long review records about another project, and the agent gave priority to the explicit task.
- **Retrieval.** Keyword search over raw review records ranked the best useful lesson 14th or lower for lock, verifier
  and fczero. For simlink it ranked 5th but was not in the memory text.
- **The one interesting cell.** On onnx, retrieved memory avoided the mistake 5 of 5 times, everything else 1 or 2. It
  is one task with 5 seeds, and the lesson was already in the repository, so it is a hint, not evidence.

## 10. Engineering challenges

- **Evaluation integrity.** Fourteen freezes before the paid grid, each with a recorded reason, all before any counted
  run. None changed the tasks, memory texts, conditions, seeds, tests or comparisons. Some refined how crashed or
  cut-off runs are handled. The analysis refuses to run if any input changed.
- **Isolation.** Single-commit redacted snapshots, containers with no network, a host sandbox that denies writes to
  the source repositories, and a before/after snapshot of every file there.
- **Privacy.** The first full paid run was stopped by its own audit: an internal branch name had reached the model
  through `git log` output. The fix was a fail-closed scan of every outgoing request, conversation history and tool
  output included. Later, when the disk filled, that boundary refused a request carrying an error message with a local
  path.
- **Budget control.** A hash-chained, append-only ledger reserves each request's worst case under a lock before it is
  sent. Ten review rounds before the grid found ways money could leak (a crash before logging, two processes spending
  at once, a reachable total of $10.02) and each was closed.
- **Reproducibility.** Every headline number is written by code into `results/`. The results reviewer recomputed the
  replay's numbers from the raw rows and they matched.

## 11. Lessons for practical agent-memory systems

These follow from this work's results, at this scale, and are not general proofs.

1. **Measure behaviour, not retrieval.** A clear offline retrieval gain (v4) showed no clear effect on a real agent (v5).
2. **Memory competes with the repository.** When the lesson is visible in the code, a strong agent finds it, and memory
   added tokens with no clear effect detected.
3. **Raw history is a poor memory format.** A one-line lesson buried in long records showed no clear effect on behaviour,
   even when it was hand-picked.
4. **Keyword retrieval over review prose misses.** The useful lesson reached the memory text for one task of five.
5. **Leaks are everywhere.** Git history in evaluation images, caches in `node_modules` and branch names in tool output
   all leaked or nearly leaked here. Check the full request, not only the prompt.
6. **Design the money path like a database.** Reserve before sending, settle after, latch every failure, never reset.

## 12. What we would investigate next (not done)

- 6 to 8 tasks where the lesson is absent from the repository, so memory has room to help.
- A distilled-lesson condition (a short, specific rule) next to raw records.
- Retrieval that indexes lessons rather than whole review documents.
- The two reporting gaps fixed before a new freeze.

None of this was built or run.

## 13. Conclusions and limitations

The evidence moved the question from "can memory find the right file?" (yes, measurably) to "does it change what the
agent does?" (no clear effect detected in these settings). The results did not clearly support the starting hypothesis, and the record shows each step
of that, including the negative ones.

Limitations: one agent and one model family for the agent experiments, four primary replay tasks (the smallest possible
p-value was 0.125, so nothing could be significant), two of them at the ceiling, memory as raw records rather than
distilled lessons, and two known reporting gaps in the frozen replay analysis (secondary comparisons without bootstrap
intervals, token counts that miss 32 format-error responses). v1 to v4 measure file overlap, a proxy. None of this
shows that agent memory does not work in general.

---

Headline numbers come from the JSON files in [`results/`](../../results/), written by the code. A few historical
details come from private run notes and are marked in the engineering log. Full engineering log: [`../JOURNEY.md`](../JOURNEY.md). Replay details: [`../REPLAY.md`](../REPLAY.md).

retrymemo · Yash Gupta · [github.com/yashgix/retrymemo](https://github.com/yashgix/retrymemo)
