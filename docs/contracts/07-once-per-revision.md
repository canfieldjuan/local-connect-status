# Contract 07 — A check runs once per revision, not once per tick

Status: **accepted 2026-09-24** (operator: "go"; rev 2, written before any code after reading every local
runner, replaces rev 1's hand-listed key with the store's own identity and corrects the decided-verdict set).
Slice 7 of the 2026-09-18 fix plan, promoted ahead of the timer work because it removes the cost the timer
work was budgeting for.
Scope: `lcstatus/collect.py` (the dispatch decision for local runners), `lcstatus/evidence.py` (the
store's series lookup, one verdict set), `lcstatus/verify.py` (one function every local runner builds its
rows from), tests, README "How it runs". No rules, render or catalogue change; no product repository.

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
- **Reproduced (journal, 2026-09-23):** the ticks that started 18:23:57 and 18:45:54 (-05:00) both ran all
  18 routine local checks against the same heads (Email Watcher `ac4829fd`, Invoice Processor `7b52f566`),
  about six minutes of wall time each (`ew.pytest.unit` 2m45s, `ip.pytest.all` 2m21s). Nothing changed in
  between.
- The five local runners each build their row's fixed fields by hand (`verify.py` `source_inspection`,
  `pytest`, `cargo_lib`, `accept_ew_ip`, `accept_ew_ds`): platform comes from the check in two and is
  hard-coded `linux` in three; the two cross-app runners hard-code their repository.

## Observable behaviour

B1. **The run key is the store's own identity, built once.** `verify.run_base(catalogue, runner, check_id,
check, revisions, condition_ids, task_ids)` returns the fields a row of that run carries before its outcome
is known: kind, repository, revision and revision time, platform, condition ids, source (type, check,
semantic check fingerprint, condition fingerprints, host) and, for a check that declares participants,
their exact heads. **Every local runner builds its rows from it**, and the collector builds the planned
row from the same call. The run key is that planned row's `series_identity()` — the identity the store
already uses to decide that a delivery repeats the observation before it. So everything that makes a row
a different observation makes a different key: the revision, any participant's head, the semantic
fingerprint (a note edit does not), a reworded condition claim, and a condition added to or removed from
the check. (Rev 1 listed the key's fields by hand and omitted the last two.)

B2. **Skip when decided.** Before launching a local runner, the collector asks the store for the latest
row in the planned row's series (`Store.latest_in_series`, the same lookup `Store.add` uses to collapse a
repeat). If that row's verdict is in `DECIDED_VERDICTS` the runner is not launched, nothing is written,
and the collector prints one `unchanged <check> ...` line naming the verdict and when it was recorded.
Otherwise the runner runs, as today. `DECIDED_VERDICTS` is exactly the verdicts in which the runner reached
the code and got an answer, read from the writers:

| verdict | written when | decided? |
|---|---|---|
| `pass` | exit 0 and every counted test passed | yes |
| `fail` | nonzero exit, a counted failure, or an incomplete acceptance proof | yes: a failure is an answer (D1) |
| `inconclusive` | a source inspection read the exact tree (the tree at a SHA never changes; paths and markers are in the fingerprint) | yes |
| `skip` | zero tests executed | no: nothing was checked |
| `unknown` | the counts could not be read | no: a harness fault |
| `unavailable` | tree, environment, tool, licence, path or timeout problem | no: a harness fault |
| `pending`, `partial` | not produced by local runners; listed for completeness | no |

Recovery therefore still happens: a check that was unavailable is retried every tick until it decides.

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
| a condition's `proves` reworded, or a condition added to the check | runs (the condition fingerprints and condition ids are in the key) |
| a check's `note` edited | skipped (the semantic fingerprint is unchanged, contract 03) |
| a check that serves no condition (`ew.pytest.unit` today) | runs once per revision like any other; whether to map it to a condition or remove it is the operator's call and not this slice |
| the store's latest row at the key is decisive but the head was not observed this tick | no local runner launches anyway (no revision to run against), as today |

## Concurrency model

None new: the lookup reads the in-memory store the tick already loaded, under the same lock.

## Settling test evidence

Unit:
0. `test_every_local_runner_builds_its_row_from_run_base`: each of the five runners, driven to an early
   `unavailable` through its real code, returns a row whose `series_identity()` equals the planned row's.
   This is what keeps B1 a single definition.
1. `test_decided_result_at_the_same_revision_is_not_rerun`: driving `collect.main` with a real store and a
   runner that records its calls: with a `pass` row at the head, the runner is not invoked and the store
   does not grow; same for `fail` and `inconclusive`.
2. `test_non_decisive_result_is_retried`: `unavailable`, `skip`, `unknown` each cause a run.
3. `test_new_revision_or_new_configuration_runs`: head moved; one participant moved; `args` edited;
   condition claim reworded; condition added. A note edit does not run.
4. `test_cross_app_key_uses_every_declared_participant`: the invoice acceptance is keyed on all three
   heads.
5. `test_rerun_and_checks_force_execution`.
6. `test_cheap_reads_still_run_every_tick`: CI, release and gate readers are invoked with a decisive row
   present.

Live, read-only, before merge: for every local check in the live catalogue at the live heads, the planned
row's series equals the series of the row main's runners last wrote at that head (so the first tick after
merge does not re-run everything once), and the skip decision is listed per check.

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

D5. **The key is the store's identity, not a new one.** The store already decides, after a run, that the
result repeats the previous observation and throws the run's files away. Making that same decision before
the run, with the same function, is the whole fix; a second, hand-listed key is how rev 1 missed claim
edits, and how slice 4 drifted three times.
D6. **Pre-existing, not changed here:** a harness failure that retries can still append a row per tick when
its details differ (eight `unavailable` rows for the invoice acceptance at today's heads).

## Estimated diff

~40 lines in `collect.py`, ~15 in `evidence.py`, ~40 in `verify.py` (one base function, five runners
calling it), ~220 lines of tests, README paragraph.
