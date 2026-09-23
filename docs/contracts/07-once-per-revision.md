# Contract 07 — A check runs once per revision, not once per tick

Status: **proposed** (review before any code). Slice 7 of the 2026-09-18 fix plan, promoted ahead of the
timer work because it removes the cost the timer work was budgeting for.
Scope: `lcstatus/collect.py` (the dispatch decision for local runners), a small read helper on the store,
tests, README "How it runs". No rules, render, runner or catalogue change; no product repository.

## Problem (evidence, not description)

- The routine timer fires every 20 minutes (`systemd/local-connect-status.timer`). On every tick the
  collector runs **every** local check — 19 today: 15 pytest selectors, the invoice acceptance, two
  source inspections, the entitlement selector — whether or not anything changed. The only selection
  logic is `wanted()` (`lcstatus/collect.py:264-270`), which looks at command-line flags, never at the
  store. Nothing asks "is there already a decisive result for this configuration at this revision?".
- The store already knows the answer: an identical result re-delivered at the same revision collapses
  (`evidence.py`, `Store.add`) and the collector then **deletes the execution's files**
  (`collect.py:64-72`, `discard_execution_files`). That is the tell: the work is done, the result is
  thrown away, tick after tick.
- Cost, from the latest recorded durations: the 19 routine local checks sum to ~331 s of test execution
  per tick; ×72 ticks a day ≈ 6.6 CPU-hours a day, most of it on `ip.pytest.all` (143 s) and
  `ew.pytest.unit` (125 s). Useful work today: 5 head moves were observed (4 Email Watcher, 1 Document
  Summarizer), so at most a few dozen check runs carried new information out of roughly 1,400 executed.
- It is the same shape as the Atlas unit gate running on every push: the trigger is time, not change.
  Here it is worse, because the trigger fires 72 times a day regardless of pushes.

## Observable behaviour

B1. **The run key.** For a local check (pytest, cargo, acceptance, source inspection) the run key is the
check's semantic fingerprint plus the exact revision(s) it would be about: the repository head for a
single-repository check, and the heads of every declared participant (contract 04 B6) for a cross-app
one. A row's key is read from what it already stores: `source.check_fingerprint`, `revision`,
`participants`.

B2. **Skip when decided.** Before launching a local runner, the collector looks for an admitted row with
the same run key. If the latest such row has a **decisive** verdict — `pass` or `fail` — the runner is
not launched and nothing is written: the existing row is the evidence, exactly as the rules already
treat it. If the latest such row is non-decisive (`unavailable`, `skip`, `unknown`, `pending`,
`partial`, `inconclusive`) or none exists, the runner runs, as today. Recovery therefore still happens:
a check that was unavailable because a tool was missing is retried every tick until it decides.

B3. **What still runs every tick.** Head observation, change detection, CI-run reads, release reads and
issue-gate reads: they are cheap API reads and they are what makes the page truthful about *which*
revision the stored evidence is about. A moved head changes the run key, so the first tick after a move
runs the affected checks once.

B4. **Force is explicit.** `--rerun` (new) ignores B2 for that invocation; `--checks` implies `--rerun`
for the named checks (an operator naming a check wants it run). `--heavy` and `--heavy-only` (contract
06) obey B2: a heavy check also runs once per revision.

B5. **The page says so.** README "How it runs" states the rule: a check runs once per revision and once
more after a non-decisive result; the 20-minute tick observes heads and reads CI, releases and issues.

## Invariants

I1. Evidence is never rewritten; the store stays append-only. Skipping writes nothing.
I2. **The page is identical with and without the skip**: a skipped run would have produced a row the
store would have collapsed. Verified live by deriving the page before and after one skipped tick.
I3. A non-decisive result is never final: it is retried on the next tick (B2).
I4. A new revision is always checked: the run key contains it (B1).
I5. A change to a check's configuration is always checked: the run key contains its fingerprint (B1).

## Failure cases

| Situation | Result |
|---|---|
| head unchanged, last row `pass` | skipped; the page's evidence row is unchanged, its date unchanged |
| head unchanged, last row `fail` | skipped: a failure at this revision is decided; it changes only when the code does (or `--rerun`) |
| head unchanged, last row `unavailable` (tool missing, timeout, harness fault) | runs again |
| head moved for one participant of a cross-app check | runs (new key) |
| check `args` edited (new fingerprint) | runs (new key) |
| operator runs `--checks ip.pytest.all` | runs regardless of the store |
| the store's latest row at the key is decisive but the head was not observed this tick | no local runner launches anyway (no revision to run against), as today |

## Concurrency model

None new: the lookup reads the in-memory store the tick already loaded, under the same lock.

## Settling test evidence

Unit:
1. `test_decided_result_at_the_same_revision_is_not_rerun`: with a `pass` row at the head, the runner is
   not invoked and the store does not grow; same for `fail`.
2. `test_non_decisive_result_is_retried`: `unavailable`, `skip`, `unknown`, `pending`, `partial`,
   `inconclusive` each cause a run.
3. `test_new_revision_or_new_configuration_runs`: head moved; one participant moved; `args` edited.
4. `test_cross_app_key_uses_every_declared_participant`: the invoice acceptance is keyed on all three
   heads.
5. `test_rerun_and_checks_force_execution`.
6. `test_cheap_reads_still_run_every_tick`: CI, release and gate readers are invoked with a decisive row
   present.

Live (after merge and fast-forward): the first tick with unchanged heads launches no local runner
(collector output shows only the reads), appends no row, keeps `data/logs` unchanged, and the page it
renders is identical to the previous one except `generated_at`; the first tick after a real head move
runs the affected checks once.

## Decisions

D1. **A decided failure is not re-run at the same revision.** The old rule re-ran a failing suite 72
times a day and got the same answer. A flaky suite is a product problem to surface, not to average away
by retrying; `--rerun` exists for the operator who wants a second look.
D2. **Non-decisive is always retried.** Those verdicts describe the harness, not the code; the next tick
may have the tool, the network, or the lock.
D3. **The key includes the semantic fingerprint, not the whole check.** Editing a note must not trigger a
re-run (contract 03).
D4. **Heavy checks obey the same rule (B4).** With this, contract 06's nightly timer only ever rebuilds
after a head move, and its hour matters much less.

## Estimated diff

~35 lines in `collect.py`, ~15 in `evidence.py` (one lookup), ~140 lines of tests, README paragraph.
