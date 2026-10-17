# Contract 07 — A check runs once per revision, not once per tick

Status: **accepted 2026-09-24** (operator: "go"; rev 2, written before any code after reading every local
runner, replaced rev 1's hand-listed key with the store's own identity; rev 3, after independent review of
the implementation, makes "decided" require positive proof about the code and puts every input that
produces a result into the key; rev 4, after a focused check of rev 3, excludes pytest collection errors,
fixes the code fingerprint to the code the process loaded, and moves the one-definition guarantee from a
syntax check to the runtime point where the planned and the actual row meet).
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
row from the same call. **Rev 4: the collector enforces it at runtime.** Every row a local runner returns
must have the planned row's series identity; one that does not is still stored (it is evidence about the
code) and the tick records a source failure `runner_identity: <check> wrote a row outside its planned
identity`, so the page says so and the next tick runs the check again. This covers every return path and
every helper a runner might call, which a syntax check cannot (the review bypassed one four ways). The run key is that planned row's `series_identity()` — the identity the store
already uses to decide that a delivery repeats the observation before it. So everything that makes a row
a different observation makes a different key: the revision, any participant's head, the semantic
fingerprint (a note edit does not), a reworded condition claim, and a condition added to or removed from
the check. (Rev 1 listed the key's fields by hand and omitted the last two.)

**Rev 3: the key covers every input that produces the result**, not only the check. The planned row's
source also carries `collector_code` — a fingerprint of the collector's own code: the package's importable
modules (regular files named `<identifier>.py` in `lcstatus/`; an editor's `.#verify.py` or a broken link
is not code Python would import and is not hashed). **Rev 4:** it is computed when the collector's modules
are imported, so a row is stamped with the code that produced it; after taking the lock the collector
hashes the package on disk again and, if it differs, exits 3 without running (the tree was updated after
this process loaded its code; the next tick runs the new code) —
and `repo_config` — a fingerprint of the catalogue entries of every repository the run touches (the
interpreter pin lives there: `repos[...].python`, read by `uv_sync_command`). `series_identity()`
includes both when present. Rows written before rev 3 carry neither, so their identity is unchanged and
the store's collapse behaves as before for them; the planned rows do carry them, so **the first tick after
this merges runs every routine local check once**, and so does the first tick after any later change to
the collector's code. That is the intended behaviour: a runner fix must be able to overturn a result the
old runner recorded.

B2. **Skip when decided.** Before launching a local runner, the collector asks the store for the latest
row in the planned row's series (`Store.latest_in_series`, the same lookup `Store.add` uses to collapse a
repeat). If `verify.decided(runner, row)` holds, the runner is not launched, nothing is written, and the
collector prints one `unchanged <check> ...` line naming the verdict and when it was recorded. Otherwise
the runner runs, as today.

**Decided means positive proof about the code** (rev 3; rev 2 treated every `fail` as decided, and the
writers record harness faults as `fail` too). `decided` holds for exactly:

| row | decided? | why |
|---|---|---|
| `pass` (any local runner) | yes | the runner's own pass criteria were met: exit 0 with counted passing tests, or a complete acceptance proof |
| `inconclusive` (source inspection) | yes | the runner read the exact tree; the tree at a SHA never changes and the paths and markers are in the fingerprint |
| `fail` from `pytest` with exit status 1 and `failed` ≥ 1 | yes | exit 1 is pytest's own "tests were collected and run and some failed"; the count is JUnit's |
| `fail` from `cargo_lib` with `failed` ≥ 1 | yes | the count comes from cargo's test-result lines, which exist only when tests ran |
| any other `fail` | no | a nonzero exit without a framework count: an out-of-memory kill, a collection error (pytest exit 2: JUnit reports it as an *error* on a test it never ran, so the runner's `failed` is 1 — rev 4 excludes it by the exit status), a usage error or no tests (pytest exit 4/5 are written `fail` with `executed` 0), an `npm install`, `uv` or build step, a network fetch, a licence the product rejects, Xvfb, an acceptance run that printed no proof. The PDF handoff writes `failed = 1` from the proof's exit code, which is not a framework count, so its `fail` is never decided |
| `skip`, `unknown`, `unavailable` | no | nothing was checked, or the harness could not produce a result |

The rule is a column of the one runner table (`verify.LOCAL_RUNNERS`), so it cannot drift from the runner
list. Recovery therefore still happens: a harness failure is retried every tick until it decides.

B6. **Outside the key.** The host environment — tool versions, the installed licence, network, umask — is
not an input the collector can fingerprint. A result that depends on it is re-evaluated when the code, the
check, a condition, a repository's catalogue entry or the collector changes, or on `--rerun`.

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
| head unchanged, last row a counted `fail` (tests ran and failed) | skipped until the code, configuration or collector changes (or `--rerun`) |
| head unchanged, last row an uncounted `fail` (killed, setup step, licence rejected, no proof) | runs again |
| head unchanged, last row `unavailable` (tool missing, timeout, harness fault) | runs again |
| the collector's own code changed (a runner fix deployed) | runs once (new `collector_code`) |
| a repository's interpreter pin changed | runs once (new `repo_config`) |
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
2. `test_non_decisive_result_is_retried`: `unavailable`, `skip`, `unknown`, and a `fail` without a
   framework count each cause a run; `test_decided_requires_positive_proof` pins the B2 table per runner.
2b. `test_every_input_that_produces_a_result_is_in_the_key`: a changed interpreter pin and a changed
   collector fingerprint each cause a run.
2c. `test_runner_row_outside_its_planned_identity_is_reported` (rev 4, replaces rev 3's syntax check): a
   runner returning a drifted row has it stored, a `runner_identity` source failure recorded, and is run
   again next tick. `test_pytest_collection_error_is_not_decided`: real pytest output for a module that
   fails to import (exit 2) is not decided; a failing assertion (exit 1) is.
2d. `test_collector_fingerprint_is_the_loaded_code`: on a copy of the package, editing any module changes
   the fingerprint, a hidden editor file, a broken link, a text file or `__pycache__` do not and do not
   raise; `test_a_collector_whose_code_changed_under_it_exits_without_running`: exit 3, no runner, no
   state written.
3. `test_new_revision_or_new_configuration_runs`: head moved; one participant moved; `args` edited;
   condition claim reworded; condition added. A note edit does not run.
4. `test_cross_app_key_uses_every_declared_participant`: the invoice acceptance is keyed on all three
   heads.
5. `test_rerun_and_checks_force_execution`.
6. `test_cheap_reads_still_run_every_tick`: with a decided local row present, the CI, release and issue
   readers are still invoked every tick; heavy checks obey the skip under `--heavy`; a cross-app check
   whose participant head was not observed is not launched; removing a condition causes a run.

Live, read-only, before merge: for every local check in the live catalogue at the live heads, the planned
row's series equals the series of the row main's runners last wrote at that head (so the first tick after
merge does not re-run everything once), and the skip decision is listed per check.

Live (after merge and fast-forward): the **first** tick runs every routine local check once (the new
`collector_code` and `repo_config` keys) and records its results; the **second** tick with unchanged heads
prints `unchanged` for every decided check and launches only checks whose latest row is not decided (today:
the invoice acceptance, `unavailable` while `/tmp/watcher-main` exists); its page is identical to the first
tick's except `generated_at`; the first tick after a real head move runs the affected checks once.

## Decisions

D1. **A counted failure is not re-run at the same revision; an uncounted one is.** Re-running a suite
whose tests ran and failed gets the same answer 72 times a day; a flaky suite is a product problem to
surface, not to average away. A failure with no framework count may be the harness, so it is retried.
`--rerun` exists for the operator who wants a second look.
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
D7. **Positive proof, not honest writers (rev 3).** The review found harness faults written as `fail` in
all four executing runners. Relabelling each writer is an open list (every new failure mode is another
case); defining "decided" by what proves the code failed is closed. How the page *labels* a harness fault
(today "check failed") is a separate, pre-existing question, filed as an issue.
D8. **The collector's own code is in the key (rev 3).** Rev 2 would have let a pass recorded under an
older, weaker acceptance rule stand forever: PR #9 changed that rule, and the 2026-09-19 invoice
acceptance `fail` (the collector did not yet stage the licence) turned into a `pass` eleven minutes after
the collector fix, at the same heads. Fingerprinting the whole package is over-inclusive by design: a
render-only change costs one extra tick of test runs; missing a module that affects runners would cost a
wrong verdict.
D9. **Enforce at the boundary, not by syntax (rev 4).** Rev 3 guarded "every row is built from the base"
with a syntax-tree test; a focused review defeated it four ways (a helper method, a nested write into
`base`, `base |=`, a rebuilt record). Each is another pattern to enumerate. The collector holds the planned
row and receives the actual one, so comparing their identities there closes the property for every path,
present and future, and costs one comparison per run.
D6. **Pre-existing, not changed here:** a harness failure that retries can still append a row per tick when
its details differ (eight `unavailable` rows for the invoice acceptance at today's heads).

## Estimated diff

~40 lines in `collect.py`, ~20 in `evidence.py`, ~70 in `verify.py` (one runner table with the decided
rule, one base function, five runners calling it, the collector fingerprint), ~320 lines of tests, README
paragraph.

## Proposed amendment — collector-owned runner environment (issue #34)

Status: **accepted 2026-10-17 at 19:41 UTC**, operator: **"I accept"**.
Accepted proposal identity: `795c9b9461b9703f9bd8e740e26e07d30b2a6e72`.
The proposal text below is retained as accepted; its proposal/STOP language records the prior gate.
Oversight proposed the amendment in mailbox note `f92c47d3`; Juan's separate acceptance below,
not that review guidance, authorizes implementation.

### Operator acceptance (recorded before implementation)

Dot asked verbatim:

> The PATH fix is ready in LCS #35: 618 tests passed, and both regressions caught the old settings. One contract gap blocks deployment: LCS currently reuses an old result even when its runner settings change. I recommend accepting amendment 795c9b9, which includes both services’ environment and launch commands in the check identity. A relevant change would trigger one fresh regular collection; history stays intact and heavy checks keep their existing schedule. Do you accept that amendment so the session can implement it and ship both fixes together?

Juan replied verbatim: **"I accept"**, at 19:41 UTC on 2026-10-17,
message `Sentinel_b119fd0314b0819184ac6939523fe29b`. Dot relayed the exact exchange to this
session; this session relayed it through oversight in mailbox note `6e2e1cc2`.
No additional heavy/model run or OCR #18 measurement is authorized by this acceptance.

### Evidence and owning boundary

Both shipped collector services omitted `/usr/local/sbin`, `/usr/sbin` and `/sbin` from `PATH`.
OCR record `df752bfb36f6eb394c3f6d8e` at source
`9e4f422c72f4af748a63a4232600f93a80750b33` counted eight packaging-test failures and 345 passes:
the inherited environment prevented dpkg from finding `ldconfig` and `start-stop-daemon`.
The routine unit's declaration originated in `26ccfebc0739f1fab9065f0c37f15c04cbccb522`,
`systemd/local-connect-status.service:10`; the heavy unit's in
`acf4fee059d1e79ee58d155d22f24821cf3e6d92`, `systemd/local-connect-status-heavy.service:10`.
Those declarations own the PATH repair; the product tests must remain intact.

The independent input-identity gap belongs to
`lcstatus/verify.py::collector_package_fingerprint`, currently lines 457–468. The package-only
fingerprint/loaded-code boundary dates to `65328d48` (the earlier fingerprint owner to `9c571187`).
It reads importable Python modules but no owned service declarations. Thus a unit-only repair
leaves the existing counted failure decided at unchanged source, even though its cause was repaired.
Adding a document-ocr exception, altering `decided`, or editing history would bypass that owner.

### Proposed B1 extension

Extend the existing **one** collector fingerprint owner to cover both:

- the same importable `lcstatus` modules covered today; and
- the shipped `[Service]` **`Environment=` and `ExecStart=` directives**, with unit identity and
  directive order retained, in **both** `systemd/local-connect-status.service` and
  `systemd/local-connect-status-heavy.service` from the same exact collector release.

These directives are collector-owned inputs, distinguished from B6's unbounded host environment.
User tool locations, standard system PATH directories and the runner invocation are consequently
inputs to `collector_code`. Do not enumerate particular tools, checks or products. The existing
`collector_code` source field and `series_identity()` remain the wire and lookup owners: no new
record field, verdict, store, label rule or parallel run-key definition.

Preserve the loaded-input guarantee: compute the combined fingerprint when the process imports its
collector, stamp planned and actual rows through the same `run_base`, and compare the same combined
inputs again after acquiring the collection lock. A relevant change on disk after loading must use
the existing exit-3 refusal before checks/state writes; it must not silently stamp old execution with
new input identity. Missing/unreadable required unit inputs must refuse rather than silently reverting
to the package-only fingerprint. Comments and unrelated non-directive unit text are not runner
environment inputs. Host tool binaries/versions, network, licences, umask, arbitrary ambient variables,
manager drop-ins and the web-only service are not newly fingerprinted or inferred from these files.

### Observable behaviour, cost and unchanged boundaries

After deployment of a changed owned directive, a previously decided local result belongs to the old
series. The normal collector creates the new planned series at the current source heads and runs the
selected local checks once. An unchanged directive set/package keeps the same series and skips
decided results. An actual result replaces no historical evidence; old rows remain append-only.
`verify.decided`, positive framework-count/proof requirements, retry semantics and explicit force
flags are unchanged. A repaired environment is a new input, not permission to rerun an unchanged gate.

**Cost:** the first rollout of this amendment changes the fingerprint once and runs the full regular
local selection once. Each later relevant unit change likewise costs one full regular local collection,
as a collector-code change already does. Both units feed one conservative fingerprint; changing either
invalidates local results as a class. No CPU/wall-time estimate is invented from the partial OCR run.
Heavy checks remain on their own timer/selection; this does not start them during a routine tick or
authorize a manual heavy/model run. Cheap observation/API reads continue as before.

The #34 rollout must pair the reviewed code with installation of its reviewed units through the
existing installer and user-manager `daemon-reload`. A new release's shipped directive bytes alone
are not evidence that the manager loaded them: verify loaded PATH and command values independently.
The post-repair OCR receipt must be produced by the regular collector under those loaded values,
with real counts, current exact source and the new fingerprint. No install, smoke observation,
promotion or OCR #18 measurement is authorized. No one-shot rerun, history repair or live hand edit
is substituted for the class fix. Before rollout, resolve sequencing so an automatic first collection
under the old manager environment is not mistaken for evidence of the repaired environment.

### Settling evidence required before merge

All automated tests use fixture collector packages, fixture units, fake runners and temporary stores;
they neither install units nor execute real package tools or product apps.

| Case | Required evidence |
| --- | --- |
| Only the routine unit's `Environment=` changes | New collector fingerprint/series; an old counted failure or pass does not suppress the selected current-head check; actual row matches planned series. |
| Only the heavy unit's `Environment=` changes | Same identity invalidation, proving neither unit is omitted; routine selection still does not launch heavy checks. |
| Only either unit's `ExecStart=` changes | Fingerprint changes for each unit; existing exact planned/actual identity enforcement remains. |
| Both relevant unit inputs and package unchanged | Fingerprint/series stable; decided local check skipped and store does not grow. |
| Only comments or unrelated unit text changes | No environment-input change; no accidental invalidation from that text. |
| A relevant unit input changes after import, before lock acquisition | Existing exit 3; no local runner/state writes, preserving the loaded-input rule. |
| Required unit input missing or unreadable | Refusal, no package-only fallback or fabricated identity. |
| New collector records after an environment change | Real outcome appended under the new existing `collector_code` field; historical rows byte-for-byte retained. |

Required mutants: omit the routine unit; omit the heavy unit; omit `Environment=`; omit `ExecStart=`.
Each must fail its owning regression. Retain the existing package-module, loaded-code, run-base,
decided-result, participant and explicit-force regressions; port their fixture inputs without weakening
their original assertions. The #34 PATH regression and both old-PATH restoration mutants remain.
The full repository suite runs once on the final implementation head using the existing repo `.venv`,
then the normal exact-head review/PASS, zero-thread, merge-commit, deployment and rollback gates apply.

### Scope and decision checkpoint

Proposed additions to Contract 07 scope: the two shipped collector unit directive inputs and their
fingerprint fixtures; the existing owner in `verify.py`, its existing loaded-input check in `collect.py`,
tests and README. The only unit behaviour change in this item remains #34's standard PATH repair.
No product repository, OCR assertion, catalogue, schema/wire, status, skip/retry or deployment-switch
rule changes are proposed.

**STOP for Juan's explicit acceptance or rejection**, relayed verbatim through oversight. Recommend
accepting the class fix with its one-collection cost; the alternative is to retain B6's current boundary
and separately decide whether a single named forced run is authorized, which would leave future
collector-environment fixes outside the run identity. This proposal authorizes neither implementation
nor merge/deployment. Keep draft PR #35 held so any accepted implementation and PATH repair can
ship together in one reviewed deployment.
