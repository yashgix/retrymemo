# Revisions of the frozen experiment

Each freeze is an annotated git tag. Earlier tags are kept.

- `replay-analysis-frozen` (`2c985fc`), `-2` (`4512d68`), `-3` (`73551c4`), 17 Nov 2026: the analysis frozen, then
  refrozen twice after independent review, before any paid run.
- `replay-analysis-frozen-4`, 17 Nov 2026: privacy repair after the first paid run (lock, none, seed 1) sent the
  snapshot branch name (a private name ending in `-base`) to the model through `git log` output. That run is kept as diagnostic evidence and
  excluded from the analysis. Its cost stays in the cumulative ledger.
  - The snapshot branch is `main`, image and network names start with `replay-`.
  - A fail-closed check scans every outgoing request (the whole history, tool output included) and sends nothing if
    it holds a blocked term (the request boundary in `replay.redact`). After every run the transcript, the source repos and the
    balance are checked again, and any failure stops the grid.
  - Three targeted redactions in the working copies, approved by the owner: lock's 15 chaos result files name the
    compose project `sendward-m1`, onnx's `SPEC.md` drops a bullet about an outside website, fczero's hygiene
    acceptance test no longer searches for a private name. No task, test or score uses these files.
  - The spend ledger (`ledger.json` in the work folder) carries every cent spent, across run folders and freezes.
  - Unchanged: the five tasks and their texts, the five arms and their memory texts, the five seeds, the withheld
    tests and scoring, and the analysis (`ANALYSIS.md`, `analyze.py`).
- `replay-analysis-frozen-5`, 17 Nov 2026: fixes from the independent second-model review of freeze 4 (NO-GO), before any
  run under it.
  - The privacy and budget gate moved into `agent/deepseek_model.py`, right before the SDK call, so no request can
    skip it. It scans the complete request, keys and model settings included.
  - Spend: every request reserves its worst case (every byte a token, none cached, the full output allowance) in the
    ledger before it is sent and is refused if that could pass the cap. The reservation becomes the real cost after
    the response and stays if the call fails. Ledger entries are keyed by full path, writes are atomic, and a ledger
    below its recorded floor is refused.
  - Any exception in a run or its checks stops the grid. A missing trajectory or usage data stops it too.
  - The freeze hashes the bytes of every installed file in the agent's environment, and the runner checks the freeze
    before any paid call. Every result row carries the freeze id, and `analyze.py` refuses rows from another freeze,
    so the excluded first run cannot enter. No statistic or rule of `ANALYSIS.md` changed.
- `replay-analysis-frozen-6`, 18 Nov 2026: fixes from the reviewer's second review (NO-GO), before any run under freeze 5.
  - The ledger is an append-only journal (`ledger.jsonl` in the work folder), each line chained to the one before by
    sha256, changed only under an exclusive file lock after re-reading it, so processes and folders share one total.
    It never counts less than DeepSeek's own balance drop since the anchor reading taken before the smoke call, and
    every result row records the journal position, so a rollback is noticed. The earlier `ledger.json` is kept as
    `ledger-v1.json`; the journal starts from the same $0.048539068.
  - Every paid model needs the ledger (the smoke script too), and the grid's stop flag is checked before every
    request, so after a stop not even a run already under way sends again.
  - The freeze hashes every file in the agent's site-packages (`.pth` files and unlisted files included) and every
    `agent/*.py`.
  - A run folder holding rows from another freeze, or rows whose ledger position is gone, is refused before any spend.
  - Unchanged: tasks, task texts, memory texts, arms, seeds, withheld tests, scoring and the statistics.
- `replay-analysis-frozen-7`, 18 Nov 2026: fixes from the reviewer's third review (NO-GO), before any run under freeze 6.
  - Open reservations count on top of the larger of settled spend and DeepSeek's balance drop, so a rolled-back
    journal cannot open room past the cap.
  - A shared stop file (`ledger.stop`) is written at once by any privacy block, missing usage, accounting failure or
    runner stop, and checked under the ledger lock before every reservation and again just before sending, so every
    process stops. Only a person removes it, and the runner will not start while it exists.
  - Any failure inside the ledger (lock, read, append, fsync) is raised as the non-retryable `LedgerInvalid`.
  - The freeze check also reports added packages and images, not only changed ones.
  - The smoke script uses the cumulative cap, with its own per-call reserve checked separately.
- `replay-analysis-frozen-8`, 18 Nov 2026: fixes from the reviewer's fourth review (NO-GO), before any run under freeze 7.
  - Every request goes out inside `Ledger.dispatch()`, under the cross-process lock: stop check, budget check,
    reservation, the SDK call and settlement. A stop takes the same lock, so it lands before a request or after it.
    The two workers' model calls no longer overlap (their tool and test steps still do).
  - A balance reading above the journal's settled spend journals the gap as spend (`reconcile`), so later costs count
    on top of it.
  - Usage must be whole, non-negative token counts that add up, or spending stops (`MissingUsage`, non-retryable).
  - A scoring error stops the grid.
- `replay-analysis-frozen-9`, 18 Nov 2026: fixes from the reviewer's fifth review (NO-GO), before any run under freeze 8.
  - Usage must have cached plus uncached input equal to the input total exactly, or spending stops.
  - A stop is a halt event in the journal (the stop file is a second signal); writing it failing is raised, never
    lost. Only `python3 -m replay.ledger --clear "<reason>"`, run by a person, lifts it, and that is journaled too.
  - A missing test report always counts as a scoring error, which stops the grid.
- `replay-analysis-frozen-10`, 18 Nov 2026: fixes from the reviewer's sixth review (NO-GO), before any run under freeze 9.
  - An accounting failure is latched (this process, and the stop file) before the ledger lock is released.
  - A failed extraction of the agent's change is a crash (the grid stops), never a score of the unchanged code.
  - The freeze also covers `recallbench/*.py`, which the memory code imports.
- `replay-analysis-frozen-11`, 18 Nov 2026: fixes from the reviewer's seventh review (NO-GO), before any run under freeze 10.
  - Failing to take or release the ledger lock is latched and non-retryable like any other accounting failure, and the
    budget check at the start of a run is inside its error handling.
  - A crash stops spending before Docker cleanup runs, and every cleanup call has a timeout.
- `replay-analysis-frozen-12`, 18 Nov 2026: fixes from the reviewer's eighth review (NO-GO), before any run under freeze 11.
  - Every harness failure inside a run, building the agent's environment included, stops spending before any cleanup,
    and a sandbox that fails to start stops spending before its own cleanup. Postgres readiness has a 90 s deadline.
  - A run that ends at the model's context limit still has its working tree extracted and scored (it was scored as
    the unchanged code before, which would have penalized the longer memory arms).
  - An unlock failure closes the lock file and is raised after latching.
- `replay-analysis-frozen-13`, 18 Nov 2026: fix from the reviewer's ninth review (NO-GO, one finding), before any run under
  freeze 12. A scoring failure, raised or returned, stops spending before the scoring container or sandbox is cleaned up.
  The lock image was rebuilt by a check run (same base commit `05b34926dcfb`, a new Docker id, since builds are not
  byte-reproducible); lock was then validated again on it, 3 repeats, with identical results and test sets.
- `replay-analysis-frozen-14`, 18 Nov 2026: the freeze looks up the Node base image by its digest alone
  (`node@sha256:dd5847a0…`). Docker drops a pulled `tag@digest` name within minutes, which made the runner's
  mandatory freeze check refuse to start (it spent nothing). Same image, same identity check.
