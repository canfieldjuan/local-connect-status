# Contract 06 — Heavy checks run on their own nightly timer

Status: **accepted 2026-09-24** (operator: "good to go" on the proposed defaults; rev 2, written before any
code after re-reading every piece of code rev 1 made claims about, corrects rev 1's isolation premise and
builds on contract 07). Slice 6 of the 2026-09-18 fix plan.
Scope: `systemd/` (one new timer and service, `install.sh`), `lcstatus/collect.py` (one selection flag,
the lock mode), `lcstatus/verify.py` (`Runner.cargo_lib` build cache, `Runner.accept_ew_ds` proof-step
home and interpreter pin), tests, README "Heavy checks". No rules or render change, no catalogue edit, no
product repository.

## Problem (evidence, not description)

- Two catalogue checks are `heavy` — `ds.cargo.lib` (Document Summarizer's Rust suite) and
  `xapp.accept_ew_to_ds` (the exact-tree Email Watcher → Document Summarizer PDF handoff) — and the
  collector selects them only with `--heavy` (`lcstatus/collect.py:275-280`). Nothing passes it: the
  only timer runs the routine collector (`systemd/local-connect-status.timer`, every 20 minutes). Four
  conditions therefore depend on a manual run: `ds.local_lib`, `connect.pdf_handoff_exact_revisions`,
  `rel.bundle_pdf_contract`, `rel.bundle_entitlement_ds` — two of them on the bundle's release gate.
- Live store (2026-09-24): `ds.cargo.lib` has one row, a manual run on 2026-09-23 (551 passed,
  1m05s compiling 438 crates of a 82.5 s run); `xapp.accept_ew_to_ds` has none, ever.
- The collector takes its lock non-blocking for every run except `--set-baseline`
  (`collect.py:147`). A nightly heavy run that starts while a routine tick holds the lock would exit 3
  and not run that night.
- `Runner.cargo_lib` (`verify.py:769`) compiles in each extracted tree's own `src-tauri/target`, so every
  new revision recompiles every dependency from nothing.
- `Runner.accept_ew_ds` (`verify.py:951`): the watcher's proof script already isolates itself — it
  creates a temporary directory (`scripts/connect-local-proof.py:503`) and points `XDG_RUNTIME_DIR`,
  `XDG_CONFIG_HOME` and `XDG_DATA_HOME` for itself and the provider into it (`:526`, `:527`,
  `:535`). **Rev 1 overstated the exposure.** What remains: the proof step runs with the operator's
  `HOME`, `XDG_CACHE_HOME` and `XDG_STATE_HOME` (`verify.py:988` builds the environment from the
  collector's own), and it launches the Document Summarizer provider under the installed app's identity,
  so the provider's WebKit cache and state can land beside the operator's installed Document Summarizer.
  The three build steps also use the operator's home, for the `uv`, `npm` and `cargo` caches — which is
  wanted.
- The contracts-test step (`verify.py:997`) runs `python` inside `uv run`, so uv chooses the interpreter;
  every other environment the collector builds passes the catalogue's pin when one is declared
  (`uv_sync_command`, `verify.py:99`). connect-contracts declares none today, so the gap is latent.
- With contract 07, a heavy check already runs at most once per revision (07 B4): a heavy run with no new
  head, configuration or collector code takes seconds.

## Observable behaviour

B1. **A nightly heavy timer.** `systemd/local-connect-status-heavy.timer` fires
`local-connect-status-heavy.service` daily at 03:30 local (`OnCalendar=*-*-* 03:30:00`,
`RandomizedDelaySec=10min`, `Persistent=true`, so a sleeping machine runs it on wake). The service runs
`python3 -m lcstatus.collect --heavy-only` with `Nice=19`, `IOSchedulingClass=idle`, `CPUWeight=20` and
`TimeoutStartSec=3h` (the runners bound each step; the unit bounds the whole). `install.sh` copies, enables
and starts both timers, and `--uninstall` disables and removes both.

B2. **`--heavy-only` selects the heavy checks and nothing else local.** Under it, `wanted()` is true exactly
for checks with `heavy: true`; head observation, change detection and the CI, release and issue reads run
as in any tick, so the heavy rows are about observed revisions. `--heavy` keeps its meaning (everything).
`--heavy-only` cannot be combined with `--heavy` or `--checks` (argument error, exit 2). The once-per-revision
rule (contract 07) applies unchanged.

B3. **The heavy run waits for the lock; a routine tick yields.** `--heavy-only` takes the lock blocking, as
`--set-baseline` already does: a routine tick holds it for seconds now (contract 07), so the heavy run waits
rather than losing its night. A routine tick that finds the lock held by a heavy run exits 3 as today and is
skipped; the page's last-run time shows when it last rendered.

B4. **One Rust build cache per repository for the Rust suite.** `cargo_lib` runs `cargo test --lib` with
`CARGO_TARGET_DIR` set to `<cache>/cargo-target/<repo>`, so dependencies compile once and each new revision
rebuilds only what changed. The npm steps are unchanged. **The PDF handoff's build is not redirected**: its
runner reads the provider binary from `src-tauri/target/release/document-summarizer` inside the tree
(`verify.py:1026`).

B5. **The proof step runs in an isolated home.** For the proof step only, `accept_ew_ds` creates a
compatibility home under `<cache>/xapp-homes/<key>` and runs the proof with `HOME` and the four XDG base
directories inside it (`isolated_xdg_env`, `verify.py:390`, as the invoice handoff does). The proof still
creates its own temporary directory inside that. The three build steps keep the operator's home so the
tool caches are shared. No licence is staged: the proof takes the contracts' test keyring and fixture
entitlements by argument. The compatibility home is removed on every exit path.

B6. **The contracts step honours the interpreter pin.** It passes `--python <pin>` to `uv run` when the
catalogue declares `repos["connect-contracts"].python`, exactly as `uv_sync_command` does; without a pin
it is unchanged. The pin is already in the run key (contract 07 `repo_config`).

B7. **Evidence shape is unchanged.** The heavy rows carry the same kinds, checks, participants and
fingerprints as before; only their producer and environment change.

B8. **README "Heavy checks"** says they run nightly at 03:30 on their own timer at idle priority, once per
revision, that the heavy run waits for a routine tick and a routine tick yields to it, that the Rust suite
shares one build cache per repository, and that the PDF proof runs in an isolated home.

## Invariants

I1. Evidence is never rewritten; the store stays append-only; one writer at a time.
I2. **No label changes at merge**: the timer is inert until its first fire; the first fire runs the two
heavy checks once each (new revisions for the collector code key, contract 07) and changes only the four
conditions that depend on them.
I3. The proof step never reads or writes the operator's `HOME` or XDG directories (B5).
I4. A routine tick and a heavy run never interleave, and the heavy run is never lost to a routine tick (B3).

## Failure cases

| Situation | Result |
|---|---|
| routine tick holds the lock at 03:30 | the heavy run waits (seconds), then runs |
| routine tick fires during the heavy run | exits 3, skipped; the next routine tick renders |
| heavy run overruns 3 h | systemd stops it; each runner writes its row after its step, so no partial row; the next night retries (an uncounted fail or unavailable is not decided) |
| `--heavy-only` with `--heavy` or `--checks` | argument error, exit 2 |
| machine asleep at 03:30 | `Persistent=true` fires on wake |
| no head moved since the last heavy run | both heavy checks print `unchanged` (contract 07); the run takes seconds |
| xvfb-run, cargo, npm or uv missing | `unavailable` rows, as today |
| the proof writes to `HOME` or an XDG directory | it lands in the compatibility home, which is removed afterwards |
| the shared Rust build cache is deleted | the next run rebuilds it; the verdict does not depend on it |

## Concurrency model

Two timers, one exclusive lock. The routine tick acquires non-blocking and yields; the heavy run acquires
blocking and waits (at most one routine tick). The shared Rust build cache is used only under the lock.

## Settling test evidence

Unit:
1. `test_heavy_only_selects_exactly_the_heavy_checks`: under `--heavy-only` the heavy check runs and a routine
   check does not; the CI, release and issue reads still run; `--heavy` still selects both; a routine tick
   selects no heavy check.
2. `test_heavy_only_cannot_be_combined`: with `--heavy` or `--checks`, exit 2.
3. `test_heavy_run_waits_for_the_lock`: with the lock held by another open file, a `--heavy-only` run blocks,
   then completes after the lock is released and runs the heavy check; a routine tick in the same
   situation exits 3 at once.
4. `test_rust_suite_shares_one_build_cache_per_repository`: the `cargo test` step carries
   `CARGO_TARGET_DIR=<cache>/cargo-target/<repo>`; the npm steps do not.
5. `test_pdf_proof_runs_in_an_isolated_home`: the proof step's `HOME` and four XDG directories are inside
   the compatibility home and none equals an inherited value; the build steps keep the collector's `HOME`;
   the compatibility home is removed after pass, fail, timeout and OSError.
6. `test_pdf_contracts_step_honours_the_interpreter_pin`: `--python 3.13` present with a pin, absent without.
7. `test_heavy_units_are_installed_and_bounded`: the unit files parse; the heavy service carries `Nice=19`,
   `IOSchedulingClass=idle`, `CPUWeight=20`, `TimeoutStartSec=3h` and `--heavy-only`; the heavy timer's
   `OnCalendar` is 03:30 daily and `Persistent=true`; `install.sh` names both timers when installing and
   when uninstalling.

Live: `systemd-analyze calendar` confirms the next elapse; after merge and fast-forward, `install.sh` is
re-run and `systemctl --user list-timers` shows both timers; the morning after, the journal of
`local-connect-status-heavy.service` shows the two heavy checks run once and their rows in the store.

## Decisions

D1. **Hour: 03:30 local**, the operator's accepted default.
D2. **The lock is not narrowed to the write.** Mirrors and extracted trees are shared state too; one writer
at a time is the property every earlier slice relies on.
D3. **The first heavy run is the timer's first fire**, at the accepted hour, not a run started by merging.
D4. **No licence is staged for the PDF handoff**: the proof takes the test keyring and fixture entitlements
by argument; staging the operator's licence would test the wrong thing.
D5. **One Rust build cache per repository (B4)**, only for the Rust suite. Cargo keys workspace artifacts by
their source path, so trees at different revisions never collide, and every dependency compiles once. The
PDF handoff is left alone because its runner reads the binary from the tree.
D6. **The heavy run waits for the lock (B3).** Rev 1 kept non-blocking acquisition, which would let a
routine tick cost the heavy run its night; since contract 07 a routine tick holds the lock for seconds.
D7. **The build steps keep the operator's home (B5).** Isolating them would re-download every `uv`, `npm`
and `cargo` dependency on each run; the tool caches are content-addressed and shared on purpose.

## Estimated diff

~40 lines of systemd units and `install.sh`, ~20 in `collect.py`, ~40 in `verify.py`, ~200 lines of
tests, README paragraph.
