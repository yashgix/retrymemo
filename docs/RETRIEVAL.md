# Retrieval benchmark (v1 and v2): recallbench

Which past coding-agent sessions should a memory inject for a new task? This project measures one part of that question on public data: given a GitHub issue, do the top-ranked sessions point to files that the issue's real merged fix changed? The memory is a set of prior SWE-agent runs, the queries are SWE-bench-extra issues, and no model or API is called.

## Why this matters

Teams whose agents share memory accumulate sessions. A memory layer must choose a few to put into the next agent's context. Retrieval quality is not the whole product, but it is a precondition. If the injected sessions point at the wrong code, the agent gets noise. If they point at the right code, the agent has a chance to use it. This benchmark isolates that recall step, with a leak-free time split, an objective file-overlap label and simple baselines that any retriever should beat, so a retriever change can be compared before an end-to-end agent run.

Result in one line: on a held-out later half of the queries, past sessions add little beyond a query-aware use of the repository's fix history. Letting sessions vote for files beats a plain file-frequency prior, but a control that lets the fixes of similar earlier issues vote, with no sessions at all, does about as well.

## What is measured

The unit of memory is one agent session. For a query task, the candidate pool is the sessions of the same repository's tasks created strictly earlier. The rankings compared include seven from v1 (`SPEC.md`): recency, BM25 on the past issue, BM25 on the full session, BM25 on file paths, reciprocal rank fusion of session text and paths (`hybrid`), a resolved-only filter of that fusion, and a staleness re-ranker. v2 (`SPEC-v2.md`) adds two simple baselines: `most_files` puts the sessions that name the most files first and ignores the query, and `past_fix_files` lists the files that earlier fixes in the repository changed most often and uses no session at all. It also adds one retriever, `vote_prior`: the top `hybrid` sessions vote for the files they name, and the votes are fused with the `past_fix_files` list. After an independent review, a control named `issue_fix_vote` was added. It ranks strictly earlier tasks by BM25 between their issue text and the query's, lets their gold files vote, and applies the same frozen fusion with `past_fix_files`. It uses no sessions and was scored with the frozen settings, with no new tuning.

The queries are split by date. Within each repository, the earlier half of its queries is the development half and the later half is the test half. Every `vote_prior` setting, and the v1 rule that picks a best retriever, was chosen on the development half and committed (tag `v2-settings-frozen`) before the test half was scored. The idea behind `vote_prior` came from a check on all queries before the split, so the test half is new to its settings but not to the idea.

The main metric is file hit@5. Each ranking gives its first 5 distinct files, walking its sessions in rank order with edited files first. It is a hit when one of them is in the task's `gold_files`, the paths the real fix changed. Holding every ranking to the same number of files matters because v1's session hit@5 counts any file named by the top 5 sessions, so a ranking that picks sessions naming many files scores higher without matching the issue better. The v1 session-level metrics are still reported unchanged, together with two memory forms, the full session text and a short four-line card, under token budgets.

## Data

The pinned files are `data/tasks.jsonl` (677 tasks) and `data/sessions.jsonl` (812 sessions). They were derived by `tools/fetch_data.py` from two CC-BY-4.0 datasets by Nebius: [SWE-bench-extra](https://huggingface.co/datasets/nebius/SWE-bench-extra) at revision `11dcbfb` (6,376 tasks) and [SWE-agent-trajectories](https://huggingface.co/datasets/nebius/SWE-agent-trajectories) at revision `68195a1` (80,036 runs). `data/MANIFEST.json` records the full revisions, every parquet file's size and sha256, the selection rule, the picked trajectory rows, the redaction rule and the sha256 of each data file. `data/LICENSE-CC-BY-4.0.txt` is the license and the two `*-README.md` files are the dataset cards.

The benchmark treats these files as fixed: it never changes, moves or regenerates them, and the experiment makes no network calls.

Selection: every SWE-bench-extra task in a repository with at least 20 tasks that have a trajectory (8 repositories), and for each task the first resolved and the first failed trajectory by source row, when they exist. Tasks: `PyCQA/pyflakes` 20, `Textualize/textual` 23, `asottile/pyupgrade` 55, `iterative/dvc` 258, `oasis-open/cti-python-stix2` 23, `pydantic/pydantic` 200, `reframe-hpc/reframe` 20, `tobymao/sqlglot` 78. Sessions: 812, of which 138 resolved and 68 point to no file. Every task has one or two sessions.

Labels and pointers were derived, not judged by hand. `gold_files` are the `b/` paths of the `diff --git` headers of the task's gold patch. The source patch leaves out test files. `edited` is the same for the session's generated patch. `opened` is the paths in SWE-agent's `(Open file: ...)` status lines, made relative to the first `(Current directory: ...)`. `resolved` is the dataset's `target`, meaning the run's patch passed the task's tests. Files that the agent's own patch added, such as scratch `reproduce.py` scripts, are left out of both pointer lists. `Session.pointers` is edited files first, then opened files, each file once.

Redaction applies to issue text and agent turns: emails become `<email>`, user names in `/home/<user>`, `/Users/<user>` and `C:\Users\<user>` paths become `<user>`, credential-like tokens become `<token>`, every `NAME=` whose name ends in `TOKEN`, `SECRET`, `API_KEY` or `PASSWORD` becomes `<redacted: NAME>` with its value removed, and an `@handle` that does not start its line becomes `@<user>`, so decorators at line start are kept. This includes 142 fake `APP_PASSWORD=` lines in agent turns. Redaction is pattern-based and can miss free-text names.

Licenses: both datasets are CC-BY-4.0, with attribution to Nebius (see [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)). This repository is a subset with derived fields and redacted text, not the original datasets. Each task row records its source repository's license, one of MIT, Apache-2.0 or BSD-3-Clause in this subset. The dataset card also says that use of the model outputs must comply with the [Llama 3.1 License](https://www.llama.com/llama3_1/license/). No model is trained on those outputs here.

Rebuild: `python tools/fetch_data.py` downloads the 13 pinned parquet files (about 1.1 GB) into `data/raw/`, checks each sha256, reapplies the selection and redaction, and rewrites the two JSONL files. `python tools/fetch_data.py --verify` checks the pinned files only and uses no network. `--source DIR` reads parquet files from a local copy.

## Run

```sh
make test
make lint
python experiment.py --out results/metrics.json
python demo.py
python demo.py --readme docs/RETRIEVAL.md --out docs/RETRIEVAL.md
python tools/fetch_data.py --verify
make tune
make misses
```

`make experiment` and `make readme` wrap the two commands above. The experiment writes `results/metrics.json` (v1) and `results/files.json` (v2) in the same folder. `demo.py` without `--readme` prints a one-line summary. With `--readme` and `--out` it replaces only the block between the marker lines in this file, and text outside the markers is unchanged. `make tune` re-runs the development-half tuning into `results/tuning.json`, and `make misses` regenerates `docs/MISSES.md`.

## Results

The block below is generated from `results/metrics.json` and `results/files.json` by `python demo.py --readme docs/RETRIEVAL.md --out docs/RETRIEVAL.md`. It is the only place in this file where measured results appear.

<!-- results:start -->
### File level, test half (main result)

Main metric, file hit@5: take the first 5 distinct files that a ranking names, walking its sessions in rank order with edited files first, and count a hit when one of them is a file the real fix changed. Every ranking is held to the same number of files, so naming more files cannot raise its score. The test half is the later half of each repository's queries (300 queries). The `vote_prior` settings and the v1-style best retriever were chosen on the development half before the test half was scored. Test half: `vote_prior` 52.7%, `issue_fix_vote` 49.7%, `past_fix_files` 40.0%, `hybrid` 43.0%, `bm25_issue` 39.7%, `most_files` 21.0%.

| Metric | Comparison | Difference (points) | 95% CI, queries | 95% CI, repositories | Reading |
|---|---|---|---|---|---|
| file_hit@5 | `vote_prior` vs `issue_fix_vote` | +3.0 | -2.7 to +8.0 | -5.3 to +7.4 | no clear difference |
| file_hit@5 | `vote_prior` vs `past_fix_files` | +12.7 | +7.0 to +18.0 | -1.4 to +19.0 | clear over queries, not over repositories |
| file_hit@5 | `vote_prior` vs `hybrid` | +9.7 | +5.3 to +14.0 | +2.7 to +18.6 | `vote_prior` is better |
| file_hit@5 | `hybrid` vs `past_fix_files` | +3.0 | -3.3 to +8.7 | -15.1 to +14.7 | no clear difference |
| file_hit@5 | `hybrid` vs `bm25_issue` | +3.3 | -1.3 to +8.3 | -6.0 to +11.1 | no clear difference |
| file_hit@5 | `hybrid` vs `most_files` | +22.0 | +16.3 to +27.3 | +13.7 to +27.0 | `hybrid` is better |
| session_hit@5 | `hybrid` vs `bm25_issue` | +12.0 | +7.3 to +17.0 | +3.7 to +15.9 | `hybrid` is better |
| session_hit@5 | `hybrid` vs `most_files` | -2.3 | -8.3 to +3.7 | -12.3 to +14.8 | no clear difference |

A difference is called clear only when both intervals exclude zero. The repository interval resamples whole repositories, so it shows how much the result depends on which repositories are in the data.

| Ranking | file hit@1 | file hit@5 | file hit@10 | hit@5 CI, queries | hit@5 CI, repositories | recall@5 | session hit@5 (v1) | files named by top 5 sessions |
|---|---|---|---|---|---|---|---|---|
| recent | 10.0% | 26.0% | 38.7% | 21.0%-31.0% | 12.1%-50.0% | 20.5% | 29.3% | 8.4 |
| bm25_issue | 18.3% | 39.7% | 50.0% | 34.7%-45.0% | 21.6%-59.7% | 31.6% | 46.3% | 8.3 |
| bm25_session | 23.0% | 40.3% | 51.3% | 35.0%-46.0% | 22.9%-61.1% | 31.2% | 50.0% | 10.2 |
| bm25_paths | 11.3% | 25.0% | 44.0% | 20.3%-30.0% | 18.1%-50.3% | 20.3% | 50.3% | 19.8 |
| hybrid | 17.7% | 43.0% | 54.7% | 37.7%-48.7% | 29.6%-64.5% | 33.1% | 58.3% | 16.1 |
| resolved_only | 20.7% | 40.7% | 49.3% | 35.3%-46.0% | 19.1%-62.9% | 32.7% | 46.3% | 8.8 |
| fresh | 10.7% | 24.0% | 35.7% | 19.3%-29.0% | 15.1%-46.3% | 18.8% | 34.0% | 9.6 |
| most_files | 6.7% | 21.0% | 27.0% | 16.3%-26.3% | 6.2%-46.2% | 16.5% | 60.7% | 84.5 |
| past_fix_files | 20.0% | 40.0% | 51.0% | 34.7%-46.0% | 16.1%-68.6% | 32.9% | n/a | n/a |
| issue_fix_vote | 23.3% | 49.7% | 61.3% | 44.7%-55.3% | 27.6%-74.7% | 41.0% | n/a | n/a |
| vote_prior | 22.0% | 52.7% | 62.7% | 47.0%-58.0% | 33.1%-75.3% | 42.5% | n/a | n/a |

File hit@5 by repository, test half:

| Repository | vote_prior | issue_fix_vote | past_fix_files | hybrid | bm25_issue | most_files | vote_prior - issue_fix_vote (95% CI, queries) | Queries |
|---|---|---|---|---|---|---|---|---|
| PyCQA/pyflakes | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | +0.0 (+0.0 to +0.0) | 5 |
| Textualize/textual | 57.1% | 100.0% | 100.0% | 28.6% | 14.3% | 0.0% | -42.9 (-85.7 to -14.3) | 7 |
| asottile/pyupgrade | 60.9% | 60.9% | 60.9% | 60.9% | 60.9% | 60.9% | +0.0 (+0.0 to +0.0) | 23 |
| iterative/dvc | 27.4% | 20.2% | 7.3% | 25.0% | 16.1% | 0.8% | +7.3 (-1.6 to +16.1) | 124 |
| oasis-open/cti-python-stix2 | 42.9% | 57.1% | 57.1% | 42.9% | 57.1% | 28.6% | -14.3 (-42.9 to +0.0) | 7 |
| pydantic/pydantic | 69.5% | 70.5% | 62.1% | 50.5% | 56.8% | 29.5% | -1.1 (-11.6 to +8.4) | 95 |
| reframe-hpc/reframe | 80.0% | 40.0% | 40.0% | 40.0% | 60.0% | 0.0% | +40.0 (+0.0 to +80.0) | 5 |
| tobymao/sqlglot | 82.4% | 73.5% | 58.8% | 70.6% | 52.9% | 38.2% | +8.8 (-2.9 to +20.6) | 34 |
| Mean of repositories | 65.0% | 65.3% | 60.8% | 52.3% | 52.3% | 32.2% |  |  |

Memory cost, test half: gold files found among the first 5 files, and the tokens needed to inject them as a plain file list, as the cards of the sessions that supplied them, or as those full sessions. Per 1k is gold files found per 1,000 injected tokens.

| Ranking | Gold files found | File list | per 1k | Cards | per 1k | Full sessions | per 1k |
|---|---|---|---|---|---|---|---|
| recent | 0.29 | 26 | 11.13 | 112 | 2.57 | 10221 | 0.03 |
| bm25_issue | 0.43 | 25 | 16.87 | 108 | 3.97 | 10306 | 0.04 |
| bm25_session | 0.44 | 25 | 17.29 | 117 | 3.74 | 10248 | 0.04 |
| bm25_paths | 0.27 | 27 | 9.95 | 97 | 2.77 | 8810 | 0.03 |
| hybrid | 0.47 | 26 | 17.88 | 104 | 4.57 | 8791 | 0.05 |
| resolved_only | 0.45 | 23 | 19.72 | 102 | 4.37 | 5798 | 0.08 |
| fresh | 0.26 | 27 | 9.44 | 103 | 2.49 | 9652 | 0.03 |
| most_files | 0.22 | 23 | 9.62 | 408 | 0.54 | 4561 | 0.05 |
| past_fix_files | 0.51 | 23 | 22.30 | n/a | n/a | n/a | n/a |
| issue_fix_vote | 0.58 | 25 | 22.97 | n/a | n/a | n/a | n/a |
| vote_prior | 0.62 | 25 | 24.68 | n/a | n/a | n/a | n/a |

### Development half and all queries

The v1 rule (highest session hit@5) applied to the development half picks `hybrid`, with session hit@5 70.0% there and 58.3% on the test half.

| Ranking | file hit@5, dev | file hit@5, test | file hit@5, all |
|---|---|---|---|
| recent | 34.3% | 26.0% | 30.2% |
| bm25_issue | 43.1% | 39.7% | 41.4% |
| bm25_session | 49.5% | 40.3% | 44.9% |
| bm25_paths | 45.1% | 25.0% | 35.0% |
| hybrid | 54.2% | 43.0% | 48.6% |
| resolved_only | 44.1% | 40.7% | 42.4% |
| fresh | 34.7% | 24.0% | 29.3% |
| most_files | 32.0% | 21.0% | 26.5% |
| past_fix_files | 51.9% | 40.0% | 45.9% |
| issue_fix_vote | 59.3% | 49.7% | 54.4% |
| vote_prior | 64.0% | 52.7% | 58.3% |

The development half is where `vote_prior` was tuned, so its development number is optimistic.

Test-half misses of `vote_prior` are classified in `docs/MISSES.md`.

### Session level, all queries (v1 metrics, unchanged)

Across 597 queries from 8 repositories (677 tasks, 812 sessions, 138 resolved sessions), the mean pool has 114.4 sessions and 510 queries have a memory that points to a file the real fix touched. The mean full-session cost is 3713 tokens and the mean card cost is 37 tokens.

| Retriever | hit@1 | hit@5 (95% CI) | recall@5 | nDCG@5 | hit@5 vs bm25_issue | tokens@5 full | tokens@5 card |
|---|---|---|---|---|---|---|---|
| `recent` | 17.8% | 37.4% (33.5%-41.4%) | 27.7% | 0.174 | -11.6 (-15.6 to -7.7) | 18636 | 185 |
| `bm25_issue` | 26.0% | 48.9% (44.7%-53.1%) | 36.8% | 0.235 | +0.0 (+0.0 to +0.0) | 19302 | 183 |
| `bm25_session` | 31.5% | 55.3% (51.3%-59.5%) | 41.0% | 0.279 | +6.4 (+3.2 to +9.5) | 18429 | 198 |
| `bm25_paths` | 21.8% | 59.1% (55.4%-63.0%) | 45.4% | 0.289 | +10.2 (+6.0 to +14.7) | 18078 | 234 |
| **`hybrid`** (best) | 33.8% | 64.2% (60.5%-68.0%) | 50.2% | 0.345 | +15.2 (+11.7 to +19.1) | 18948 | 241 |
| `resolved_only` | 28.5% | 47.2% (43.2%-51.1%) | 35.9% | 0.197 | -1.7 (-5.9 to +2.3) | 9934 | 168 |
| `fresh` | 17.4% | 40.7% (36.9%-45.2%) | 28.0% | 0.143 | -8.2 (-12.4 to -3.7) | 18012 | 181 |

| Budget | recent full | recent card | hybrid full | hybrid card |
|---|---|---|---|---|
| 500 | 0.8% | 54.8% | 1.7% | 72.2% |
| 1000 | 6.2% | 60.6% | 10.7% | 75.7% |
| 2000 | 14.9% | 62.1% | 23.3% | 79.2% |
| 4000 | 24.8% | 62.1% | 38.7% | 79.2% |
| 8000 | 30.8% | 62.1% | 49.7% | 79.2% |
| 16000 | 38.5% | 62.1% | 64.5% | 79.2% |

Both forms are scored on the same file pointers, so the card's lead in this table is a budget effect, true by construction: more cards fit. It does not show that a card helps an agent as much as a full session.

| Repository | Queries | Answerable | recent | bm25_issue | hybrid |
|---|---|---|---|---|---|
| PyCQA/pyflakes | 10 | 10 | 100.0% | 100.0% | 100.0% |
| Textualize/textual | 13 | 5 | 15.4% | 23.1% | 38.5% |
| asottile/pyupgrade | 45 | 37 | 82.2% | 80.0% | 80.0% |
| iterative/dvc | 248 | 195 | 16.9% | 26.2% | 47.6% |
| oasis-open/cti-python-stix2 | 13 | 8 | 30.8% | 46.2% | 53.8% |
| pydantic/pydantic | 190 | 183 | 44.2% | 61.1% | 73.7% |
| reframe-hpc/reframe | 10 | 8 | 60.0% | 70.0% | 60.0% |
| tobymao/sqlglot | 68 | 64 | 55.9% | 72.1% | 89.7% |
<!-- results:end -->

## What this shows

Read the generated block as a comparison, not a leaderboard. The main result is the test half at file level.

- `vote_prior` beats the file-frequency prior `past_fix_files` on the test half, clearly over queries but not over repositories, and it clearly beats `hybrid`. It does not clearly beat `issue_fix_vote`, the control that uses the fixes of similar earlier issues and no sessions, and the two have about the same mean over repositories. So the gain is over the file-frequency prior. On this data, session memory adds little beyond a query-aware use of fix history. That is the result.
- dvc is where sessions add the most over `issue_fix_vote`. dvc moved code over time: its fixes touch `dvc/command/` until December 2021 and `dvc/commands/` from February 2022. But within dvc the interval for `vote_prior` minus `issue_fix_vote` still includes zero, so this is a hint, not a clear case.
- Session retrieval alone (`hybrid`) does not clearly beat `past_fix_files`, or BM25 on the past issue, at file level on the test half. On the v1 session metric it scores about the same as `most_files`, a ranking that ignores the query.
- Every ranking scores lower on the test half than on the development half, and the main rankings drop by similar amounts. These numbers cannot tell whether the later tasks are harder or the development choices were optimistic, so the development numbers are not results.
- Down-weighting sessions that name very long file lists did not help on the development half, so the frozen setting does not use it.
- Cost: a plain list of five file paths costs a few dozen tokens. The cards that supply the same files cost a few times more, and the full sessions cost hundreds of times more. If the goal is pointing an agent at files, a file list is the cheapest memory form. Whether the cards' or sessions' extra text helps an agent is not measured here.
- `docs/MISSES.md` classifies the test-half misses of `vote_prior`. Most gold files do appear in an earlier session, but in one ranked below the few that vote. Some are new to the history, and no history-based method can find those.

A higher file hit means the memory is more likely to point at a file the real fix touched. It does not prove an agent would solve more tasks, because no agent was re-run with the memories.

## Prior art

The methods are standard. The contribution is the time-split recall benchmark on public agent sessions.

- BM25: Robertson and Zaragoza, "The Probabilistic Relevance Framework: BM25 and Beyond", 2009.
- Reciprocal rank fusion: Cormack, Clarke and Buettcher, SIGIR 2009.
- nDCG: Järvelin and Kekäläinen, 2002.
- SWE-bench: Jimenez et al., [arXiv 2310.06770](https://arxiv.org/abs/2310.06770).
- SWE-agent: Yang et al., [arXiv 2405.15793](https://arxiv.org/abs/2405.15793).
- File-level localization as a step of agentic repair: Agentless, Xia et al., [arXiv 2407.01489](https://arxiv.org/abs/2407.01489).

## Limits

- File overlap with the real fix is a proxy for useful context. A memory can help with a decision or a pitfall without naming a gold file, or name a gold file and still mislead. No agent was re-run with the selected memories.
- The sessions are SWE-agent runs by Nebius's models on old commits, not a real team's sessions. There are one or two per task, and 68 of 812 point to no file. Pointer lists include files an agent merely opened.
- Time order is the PR creation time. Sessions were generated later on each task's own base commit, so the pool is leak-free with respect to the issue but is not a wall-clock replay of a team.
- The data covers 8 Python repositories, dominated by dvc (248 queries) and pydantic (190). Per-repository numbers for the small repositories rest on 10-13 queries.
- Tokens are estimated at four characters per token, not with a model's tokenizer.
- The card and the full session are compared only on cost under a budget. Whether the card's missing reasoning matters to an agent is not measured.
- `past_fix_files` uses the files of fixes from PRs created before the query. It assumes those fixes were merged by then, which the data does not record.
- The staleness re-ranker tested here is one simple rule (0.8 per later fix). Its result says nothing about staleness signals in general.
- The v1 constants (the 0.8 decay, the hybrid's two parts, `RRF_K`, `MIN_HISTORY`, the budgets and the card format) were set after a prototype run on all of this data, before the date split existed. The `vote_prior` idea also came from a check on all queries. Only the `vote_prior` settings and the dev-half retriever choice were made on the development half alone.
- The test half has 300 queries from 8 repositories, so the repository interval is wide and the small repositories' rows rest on 5-34 queries.
- Personal data was redacted by pattern. Free-text names in issues may remain, and @handles at the start of a line are kept by the rule that protects Python decorators, so some GitHub handles remain.
