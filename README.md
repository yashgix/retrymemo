# retrymemo

Does memory stop coding agents from repeating mistakes? This repository holds six experiments (v1 to v5 and a final
replay) that tried to answer that question, from an offline retrieval benchmark on public agent runs to a controlled 125-run replay with a real
coding agent. v1 was built first and then reviewed. From v2 on, each experiment was pre-registered and frozen before its held-out
data was scored, and every step was checked by an independent reviewer.

The short answer, in the settings tested here: memory **pointed to the right file** more often in an offline ranking,
but no clear effect on **what a real agent did** was detected. Better retrieval was not the bottleneck. In the final
replay every run passed its task with or without memory, and none of the three pre-registered memory comparisons
showed a clear effect.
These are results for one model, one agent and a handful of tasks, not a verdict on agent memory in general.

The full story with charts is in the case study ([`docs/case-study/index.html`](docs/case-study/index.html), Markdown
copy [`docs/case-study/CASE-STUDY.md`](docs/case-study/CASE-STUDY.md)).

## Why agent memory is hard to evaluate

- **Leakage is easy.** A memory built from later sessions, a git history that still holds the fix, or a cache left in
  `node_modules` can all hand the agent the answer. Every experiment here cuts memory by time: v1 to v4 by task creation
  date (not by when a session ended or a fix merged), the replay by when each review record was written.
- **Proxies flatter.** "The memory names the right file" is cheap to measure and easy to improve, but an agent that
  already finds the file by step 2 gains little from being told.
- **Strong agents hit the ceiling.** When the lesson is already visible in the repository, a capable model avoids the
  mistake anyway, so memory has nothing left to add.
- **Small samples are noisy.** Run-to-run variation of a few points is normal, so a single positive cell means little.

## The experiments

| # | Question | Data | Main result | Status |
|---|---|---|---|---|
| v1 | Do the top-ranked past agent sessions point to the files the real fix changed? | 677 SWE-bench-extra tasks, 812 SWE-agent sessions, 8 repos | `hybrid` 64.2% session hit@5, but a query-blind ranking scored about the same (metric confound) | superseded by v2 |
| v2 | With a fair file-level metric and a held-out half, do sessions beat fix history? | 300 held-out queries | `vote_prior` 52.7% file hit@5 vs 49.7% for a no-session control, +3.0 [-2.7, +8.0]: no clear gain | complete |
| v3 | Does any session signal beat a strong fix index on unseen tasks? | 171 unseen queries, 27 repos | session index 70.2% vs fix index 74.9%, -4.7 [-8.8, -1.2]: sessions worse | complete, negative |
| v4 | Can a failed attempt's memory point the next attempt on the same issue to the right file? | 442 held-out retries, 361 repos | hit@1 69.0% vs 60.0% for the real retry, +9.0 [+4.8, +14.3] (pre-registered). 63.3% for a fairer baseline, +5.7 (post hoc) | complete, positive (file identification only) |
| v5 | Does that file handoff help a real agent? | 144 tasks, 576 runs, DeepSeek Flash + mini-swe-agent | steps 19.73 to 19.19 (-2.7%, interval includes 0), solved 86.1% vs 87.5% | complete, no clear effect |
| replay | Do remembered review lessons stop an agent repeating a reviewer-flagged mistake? | 5 tasks × 5 memory conditions × 5 seeds = 125 runs | all 125 passed their task, and the three primary comparisons read "no clear effect" | complete, stopped |

The v4 numbers measure **file identification**, not task completion. v5 is where that was tested with a real agent,
and it showed no clear gain.

Details per experiment: [`docs/JOURNEY.md`](docs/JOURNEY.md) (chronological engineering log),
[`docs/RETRIEVAL.md`](docs/RETRIEVAL.md) (v1 and v2), [`docs/UNSEEN.md`](docs/UNSEEN.md) (v3),
[`docs/RETRY-HANDOFF.md`](docs/RETRY-HANDOFF.md) (v4), [`SPEC-v5.md`](SPEC-v5.md) (v5),
[`docs/REPLAY.md`](docs/REPLAY.md) and [`replay/ANALYSIS.md`](replay/ANALYSIS.md) (the final replay).

## The final replay in one table

Runs that avoided the repeated mistake, out of 5 seeds per cell (`results/replay-analysis.json`):

| Task | No memory | Instructions | Retrieved | Hand-picked lesson | Random records | Lesson already in repo? |
|---|---:|---:|---:|---:|---:|---|
| lock | 5 | 5 | 4 | 5 | 5 | yes |
| verifier | 5 | 5 | 5 | 5 | 5 | yes |
| onnx | 2 | 1 | 5 | 2 | 2 | yes |
| fczero | 0 | 0 | 0 | 0 | 0 | no |
| simlink (reported separately) | 5 | 5 | 5 | 5 | 5 | no, the lesson is the task |

| Pre-registered comparison (lock, verifier, onnx, fczero) | Mean difference | 95% bootstrap | Reading |
|---|---:|---|---|
| hand-picked lesson vs none | 0.00 | [-0.15, 0.15] | no clear effect |
| retrieved vs none | +0.10 | [-0.20, 0.45] | no clear effect |
| hand-picked lesson vs random | 0.00 | [-0.15, 0.15] | no clear effect |

lock and verifier (and simlink) sit at the ceiling. fczero sits at the floor: every run passed its task, but none
fixed the earlier mistake, even with the hand-picked lesson. onnx is the one interesting cell: retrieved memory 5 of 5 against 1 or 2 elsewhere. One task with 5 seeds is a hint,
not evidence. Keyword retrieval put the useful lesson into the memory text for only one task of five. The instructions, oracle and
random texts cost 15 to 18% more per run than no memory (retrieved about 1% more), with no clear effect detected. Total spend for the replay was $4.53 of a $10 cap, smoke test and an
excluded first run included.

## Architecture

```
recallbench/   v1-v4 offline benchmark: data loading, BM25 and fusion rankers, metrics, bootstrap, v4 handoff
tools/         data rebuild (sha256-pinned Hugging Face files), tuning, v3 unseen scoring, v4 handoff scoring
agent/         v5: mini-swe-agent runner, DeepSeek model with the privacy gate, handoff text, v5 analysis
replay/        final replay: tasks, frozen memory texts, workspaces, sandbox guard, spend ledger, scoring, analysis
results/       every reported number, as JSON written by the code
tests/         unit, leakage and acceptance tests (unittest)
docs/          per-experiment write-ups, the engineering log, the case study
```

The replay harness, in one line per stage: a redacted single-commit snapshot of each task repo is built into a Docker
image, the agent works in a container with no network, every model request passes a fail-closed privacy check and a
hash-chained spend ledger before it leaves the machine, and the result is scored in a fresh container with withheld
tests. A host sandbox denies writes to the source repositories, and a before/after snapshot proves they did not
change. See [`docs/REPLAY.md`](docs/REPLAY.md) for the data-flow diagram.

## Reproduce

```sh
make test          # unit, leakage and acceptance tests
make lint          # ruff check and format
make data-check    # verify the pinned v1-v4 data files, no network
make experiment    # v1 and v2 results into results/
make readme        # regenerate the results block in docs/RETRIEVAL.md
python tools/unseen.py --source data/raw --expand          # v3 (pinned parquet files, see tools/fetch_data.py)
python tools/attempts.py ...                               # v4 step 1: one record per pinned attempt
python tools/handoff.py --source data/raw --attempts <file> --split test --edited-weight 2 --out results/handoff.json
```

`tools/fetch_data.py` downloads the 13 pinned parquet files (about 1.1 GB) with sha256 checks. v3 and v4 read those
files, and their exact frozen settings are in `SPEC-v3.md` and `SPEC-v4.md` (tags `v3-method-frozen`, `v4-method-frozen`).

The v1 to v4 experiments are fully offline and reproducible from public data. v5 and the replay need paid DeepSeek
calls (the key comes from `DEEPSEEK_API_KEY`) and Docker images.

**The replay cannot be rerun from this repository.** The 125 memory texts (`replay/memory/<task>/*.md`) and the task
code and tests (each task's patches, withheld tests and inputs) are not included, because they come from the author's
private projects. Neither are those projects, which the replay reads from `REPLAY_SOURCES`. What is included: the
harness code, each task's `task.json` (with public project names), `replay/memory/manifest.json` reduced to sizes,
counts and sha256 hashes, and every result. `replay/FROZEN.json` lists the hash of every frozen input, so the freeze
check (`python3 -m replay.freeze --check`, which `replay.analyze` and paid runs require) needs those missing inputs and
fails on this copy, where some frozen files also had private names and paths replaced. The redaction code keeps its
generic patterns (emails, keys, tokens, home paths) and reads the private terms from an optional, git-ignored
`replay/private_terms.json`. Tests that need the missing inputs or Docker are skipped with the reason.

## Limitations

- Without a private terms file (`replay/private_terms.json`, git-ignored), the request boundary in `replay/redact.py`
  blocks only the API key. Anyone reusing the harness should add their own private terms there.

- One agent (mini-swe-agent) and one model family (DeepSeek Flash) for v5 and the replay.
- The replay has four primary tasks. With four tasks the smallest possible p-value is 0.125, so no comparison could be
  significant, by design. It is an exploratory case study.
- Three of the four primary replay tasks already show the lesson in the repository. On the one task where memory adds
  new information, fczero, no run avoided the earlier mistake, even with the lesson (every run passed the task itself).
- Replay memory is raw review records cut to 18,000 characters, not distilled lessons. Nothing here tests distilled memory.
- Two known reporting gaps in the frozen replay analysis, left unchanged: the secondary comparisons have no bootstrap
  intervals, and token counts leave out 32 paid responses that followed format errors (costs are complete).
- v1 to v4 measure file overlap with the real fix, a proxy for useful context.

## Data and licences

The v1 to v4 data under `data/` is a redacted subset of two Nebius datasets, [SWE-bench-extra](https://huggingface.co/datasets/nebius/SWE-bench-extra)
and [SWE-agent-trajectories](https://huggingface.co/datasets/nebius/SWE-agent-trajectories), both CC-BY-4.0, with
attribution to Nebius. The trajectories' dataset card adds that use of the model outputs must follow the
[Llama 3.1 License](https://www.llama.com/llama3_1/license/). Details in [`docs/RETRIEVAL.md`](docs/RETRIEVAL.md#data).
v5 used [SWE-rebench](https://huggingface.co/datasets/nebius/SWE-rebench) task rows (CC-BY-4.0) and its public
evaluation images. This project is independent research and is not affiliated with or endorsed by any company.

The code is under the MIT licence ([`LICENSE`](LICENSE)). By Yash Gupta.
