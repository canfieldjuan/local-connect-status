# Contract 06 — Heavy checks run on their own nightly timer

Status: **accepted 2026-09-24** (operator: "good to go" on the proposed defaults; rev 2, written before any
code after re-reading every piece of code rev 1 made claims about, corrected rev 1's isolation premise and
built on contract 07; rev 3, after independent review of the implementation, **withdraws the shared Rust
build cache** (it could run a previous revision's binary under a new SHA), removes the unit's time limit,
adds two retry attempts a night, and makes the proof own its process group; rev 4, after a focused check
of rev 3, makes that ownership complete: every heavy step, every exit path, output decoded without failing,
and the group waited for until it is gone). Slice 6 of the 2026-09-18 fix plan.
Scope: `systemd/` (one new timer and service, `install.sh`), `lcstatus/collect.py` (one selection flag,
the lock mode, catalogue load order), `lcstatus/verify.py` (`Runner.accept_ew_ds` proof-step home, process
group and interpreter pin), tests, README "Heavy checks". No rules or render change, no catalogue edit, no
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
- `Runner.accept_ew_ds` (`verify.py:951`): the watcher's proof script already isolates itself — it
  creates a temporary directory (`scripts/connect-local-proof.py:581` at the watcher head `ac4829fd`) and
  points `XDG_RUNTIME_DIR`, `XDG_CONFIG_HOME` and `XDG_DATA_HOME` for itself and the provider into it
  (`:610`, `:611`, `:622`). **Rev 1 overstated the exposure.** What remains: the proof step runs with the operator's
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
- The proof step's timeout (`subprocess.run(..., timeout=1800)`) kills only `xvfb-run`; `xvfb-run` runs its
  command as a child, so Xvfb, the proof and the provider are orphaned and keep running.

## Observable behaviour

B1. **A nightly heavy timer with two retries.** `systemd/local-connect-status-heavy.timer` fires
`local-connect-status-heavy.service` at 03:30, 04:30 and 05:30 local (`OnCalendar=*-*-* 03,04,05:30:00`,
`RandomizedDelaySec=10min`, `Persistent=true`: a machine that was off catches up once at boot; one that was
suspended fires on resume). The service runs `python3 -m lcstatus.collect --heavy-only` with `Nice=19`,
`CPUWeight=20` and `IOSchedulingClass=idle` (honoured only by an I/O scheduler that supports priorities; the
root NVMe here uses `none`, so the idle priority that applies is CPU), and **no time limit**
(`TimeoutStartSec=infinity`), for the same reason the routine unit has none: every runner bounds each of its
own steps and records the timeout as evidence, and a unit limit would kill the run before it could record,
save state or render. Because a heavy check runs once per revision (contract 07), the second and third
attempts cost seconds when the first succeeded, and retry what the first could not run. `install.sh`
copies, enables and starts both timers, and `--uninstall` disables and removes both.

B2. **`--heavy-only` selects the heavy checks and nothing else local.** Under it, `wanted()` is true exactly
for checks with `heavy: true`; head observation, change detection and the CI, release and issue reads run
as in any tick, so the heavy rows are about observed revisions. `--heavy` keeps its meaning (everything).
`--heavy-only` is a mode of its own: combined with `--heavy`, `--checks` (with or without ids), `--no-local`,
`--render-only` or `--set-baseline` it is an argument error (exit 2) before any side effect. A wanted check
whose repository or participant head was not observed that tick is not run and the collector prints
`skipped <check>: head of <repos> not observed this tick`; the next attempt retries it. The once-per-revision
rule (contract 07) applies unchanged.

B3. **The heavy run waits for the lock; a routine tick yields.** `--heavy-only` takes the lock blocking, as
`--set-baseline` already does; a routine tick holds it for seconds when no head moved (contract 07) and for
the length of its checks when one did, so the heavy run waits rather than losing its attempt. Whoever must
wait prints `waiting for the collection lock` first. The catalogue is read **after** the lock is taken, so
a run uses the catalogue current when it starts working, not when it started waiting. A routine tick that
finds the lock held exits 3 as today and is skipped; the page's last-run time shows when it last rendered.
A heavy run whose code changed on disk while it waited exits 3 (contract 07 rev 4); the next attempt runs
the new code.

B4. **Withdrawn (rev 3): no shared Rust build cache.** Each extracted tree keeps compiling in its own
`src-tauri/target`, as before. See D5.

B5. **The proof step runs in an isolated home.** For the proof step only, `accept_ew_ds` creates a
compatibility home under `<cache>/xapp-homes/<key>` and runs the proof with `HOME` and the four XDG base
directories inside it (`isolated_xdg_env`, `verify.py:390`, as the invoice handoff does). The proof still
creates its own temporary directory inside that. The three build steps keep the operator's home so the
tool caches are shared. No licence is staged: the proof takes the contracts' test keyring and fixture
entitlements by argument. **Every heavy step owns its process group** (rev 3 for the proof, rev 4 for all):
the PDF handoff's contracts, npm, build and proof steps and the Rust suite's npm and cargo steps run through
`run_owned_group`, which starts the step in a new session and, on a timeout **or any other exit from the wait
— an interrupt included** — kills the whole group (for the proof: `xvfb-run`, Xvfb, the proof and the
provider), drains its output, reaps the step, and waits until no process of the group remains before
returning. Output is decoded as UTF-8 with replacement, so a byte sequence cut by the kill cannot turn a
timeout into a crash. Nothing therefore writes into the home after the step returns. Before starting, the runner removes every stale
`pdf-*` home in `<cache>/xapp-homes` (a decided run never returns to its key, so a leftover would otherwise
stay forever); a failure to prepare the home is an `unavailable` row, not an aborted tick. After the proof the
home is removed; a removal that fails is printed to the journal and swept by the next PDF run.

B6. **The contracts step honours the interpreter pin.** It passes `--python <pin>` to `uv run` when the
catalogue declares `repos["connect-contracts"].python`, exactly as `uv_sync_command` does; without a pin
it is unchanged. The pin is already in the run key (contract 07 `repo_config`).

B7. **Evidence shape is unchanged.** The heavy rows carry the same kinds, checks, participants and
fingerprints as before; only their producer and environment change.

B8. **README "Heavy checks"** says they run nightly at 03:30 on their own timer at idle priority, once per
revision, that the heavy run waits for a routine tick and a routine tick yields to it, that the Rust suite
shares one build cache per repository, and that the PDF proof runs in an isolated home.

## Invariants

I1. Evidence is never rewritten; the store stays append-only; one collector at a time. (Pre-existing and out
of scope: `scripts/record_observation.py` appends a manual observation without taking the lock; slice 8.)
I2. **No label changes at merge**: the timer is inert until its first fire; the first fire runs the two
heavy checks once each (new revisions for the collector code key, contract 07) and changes only the four
conditions that depend on them.
I3. The proof step never reads or writes the operator's `HOME` or XDG directories (B5).
I4. A routine tick and a heavy run never interleave, and the heavy run is never lost to a routine tick (B3).

## Failure cases

| Situation | Result |
|---|---|
| routine tick holds the lock at 03:30 | the heavy run prints that it is waiting, waits, then runs |
| routine tick fires during the heavy run | exits 3, skipped; the next routine tick renders |
| a heavy step hangs | the runner's own step timeout ends its whole process group and records `unavailable`; the unit sets no limit that could pre-empt that record. Until then routine ticks exit 3 and the page is not re-rendered: at most the sum of the heavy steps' timeouts (about 5.5 hours in the worst case); the page's generated time shows it |
| a manual `--heavy` or `--checks xapp.accept_ew_to_ds` run is interrupted (Ctrl-C) | the running step's whole group is killed before the collector exits |
| the proof times out mid-way through a multi-byte character | decoded with replacement; `unavailable`, not a crash |
| a timer elapse arrives while an attempt is still running | systemd merges it into the running start job: absorbed, not queued. A machine that boots after 05:30 catches up with one attempt |
| a head cannot be read at 03:30 (network not up after boot) | the heavy check is printed as skipped; the 04:30 and 05:30 attempts retry it |
| the proof times out | its whole process group is killed, drained, reaped and waited for; `unavailable`; the home is removed afterwards. Known limit: the killed proof and `xvfb-run` cannot clean their own temporary directories, so `/tmp/connect-proof-*` and `/tmp/xvfb-run.*` remain until the next boot (redirecting `TMPDIR` into the home would lengthen socket paths toward the 108-byte limit in code that has never run under the collector) |
| the proof's home cannot be prepared (a stale entry cannot be removed) | `unavailable`, and the tick continues |
| the catalogue changes while the heavy run waits for the lock | the run uses the new catalogue |
| `--heavy-only` with `--heavy`, `--checks`, `--no-local`, `--render-only` or `--set-baseline` | argument error, exit 2, nothing created |
| machine asleep at 03:30 | `Persistent=true` fires on wake |
| no head moved since the last heavy run | both heavy checks print `unchanged` (contract 07); the run takes seconds |
| xvfb-run, cargo, npm or uv missing | `unavailable` rows, as today |
| the proof writes to `HOME` or an XDG directory | it lands in the compatibility home, which is removed afterwards |

## Concurrency model

Two timers, one exclusive lock. The routine tick acquires non-blocking and yields; the heavy run acquires
blocking and waits (at most one routine tick). The proof's process group is owned by its runner and never
outlives the step.

## Settling test evidence

Unit:
1. `test_heavy_only_selects_exactly_the_heavy_checks`: under `--heavy-only` the heavy check runs and a routine
   check does not, and the reads still run; on a fresh store a routine tick selects no heavy check and
   `--heavy` selects both.
2. `test_heavy_only_is_a_mode_of_its_own`: with `--heavy`, `--checks` (with and without ids), `--no-local`,
   `--render-only` or `--set-baseline`, exit 2 and no data directory is created.
3. `test_heavy_run_waits_for_the_lock`: with the lock held, a routine tick exits 3 at once (bounded by a
   thread join, so a regression fails instead of hanging); a `--heavy-only` run prints that it is waiting,
   blocks, and after release runs the heavy check **from the catalogue as it was when the lock was released**.
4. `test_rust_suite_builds_in_its_own_tree`: the `cargo test` step inherits the environment; no
   `CARGO_TARGET_DIR` is set.
5. `test_pdf_proof_runs_in_an_isolated_home`: the proof step's `HOME` and four XDG directories are inside the
   compatibility home and none equals an inherited value; the build steps keep the collector's `HOME` **and
   XDG directories**; the home is removed after pass, fail, timeout and OSError; a stale `pdf-*` home from an
   earlier run is swept first; a home that cannot be prepared is `unavailable`; a failed removal is printed.
6. `test_pdf_contracts_step_honours_the_interpreter_pin`.
7. With real processes: `test_the_proof_owns_its_process_group` (a command that leaves a long-lived
   background child and outlives its timeout raises the timeout within a bound, and the child is dead);
   `test_owned_group_is_gone_when_the_timeout_returns` (no process of the group remains);
   `test_owned_group_decodes_any_bytes` (undecodable output, with and without a timeout);
   `test_owned_group_ends_the_group_on_interrupt` (SIGINT to a collector-like process kills its step's
   background child). `test_heavy_steps_all_run_in_owned_groups`: every build step of both heavy runners goes
   through `run_owned_group`, never `subprocess.run`.
8. `test_heavy_units_are_installed_and_bounded`: the unit files parse; the heavy service's `ExecStart` is the
   routine one plus `--heavy-only`, its `Environment` and `WorkingDirectory` equal the routine unit's, it
   carries `Nice=19`, `CPUWeight=20`, `IOSchedulingClass=idle` and `TimeoutStartSec=infinity`; the heavy
   timer fires at 03:30, 04:30 and 05:30 with `RandomizedDelaySec=10min`, `Persistent=true` and
   `WantedBy=timers.target`; `install.sh` names both timers when installing and when uninstalling.
9. `test_unobserved_participant_head_launches_nothing` also asserts the printed skip line.

Live: `systemd-analyze calendar` confirms the next elapses; after merge and fast-forward, `install.sh` is
re-run and `systemctl --user list-timers` shows both timers; the morning after, the journal of
`local-connect-status-heavy.service` shows the two heavy checks run once, the later attempts printing
`unchanged`, and their rows in the store. The PDF proof has never run under the collector; its first run is
the first real test of B5, and a failure there is an uncounted `fail`, retried by the next attempt
(contract 07).

## Decisions

D1. **Hour: 03:30 local**, the operator's accepted default, with retries at 04:30 and 05:30 (rev 3).
D2. **The lock is not narrowed to the write.** Mirrors and extracted trees are shared state too; one writer
at a time is the property every earlier slice relies on.
D3. **The first heavy run is the timer's first fire**, at the accepted hour, not a run started by merging.
D4. **No licence is staged for the PDF handoff**: the proof takes the test keyring and fixture entitlements
by argument; staging the operator's licence would test the wrong thing.
D5. **No shared Rust build cache (withdrawn in rev 3).** Rev 2 claimed cargo keys workspace artifacts by
their absolute source path. It does not: a workspace member's metadata hash uses its path relative to the
workspace root, so trees at different revisions share one set of artifacts, and freshness falls back to file
times. `git archive` stamps every file with the commit time, so a revision whose commit landed before the
previous build finished looks older than that build, and cargo would run the previous revision's test binary
and record its result against the new SHA — a false `pass` that contract 07 would then treat as decided.
Saving a minute of compilation at 03:30 is not worth a verdict that can be about the wrong code.
D6. **The heavy run waits for the lock (B3).** Rev 1 kept non-blocking acquisition, which would let a
routine tick cost the heavy run its night; since contract 07 a routine tick holds the lock for seconds.
D7. **The build steps keep the operator's home (B5).** Isolating them would re-download every `uv`, `npm`
and `cargo` dependency on each run; the tool caches are content-addressed and shared on purpose.

D8. **No unit time limit (rev 3).** Rev 2 set `TimeoutStartSec=3h`, below the sum of the runners' own step
timeouts (about 5.5 hours in the worst case). systemd's SIGTERM ends Python without running `finally` blocks,
so a killed run records nothing. The routine unit already runs without a limit for exactly this reason.
D9. **Retries are more attempts, not restart logic (rev 3).** Once-per-revision makes a repeated attempt a
no-op when the previous one decided, so firing three times a night retries what failed (an unobserved head
after boot, a code change during the lock wait) without exit-code-specific restart rules.
D10. **Every heavy step owns its process group (rev 3, completed in rev 4).** Killing only the direct child
on timeout leaves Xvfb, the proof and the provider running and writing, and leaves node, rustc or a
deadlocked test binary burning CPU. Rev 3 applied this to the proof's timeout only; a focused check found
the other exits (an interrupt on a manual run, a strict decode after the kill) and the other steps. The
rule is now one helper for every heavy step and every exit from its wait.

## Estimated diff

~40 lines of systemd units and `install.sh`, ~30 in `collect.py`, ~70 in `verify.py`, ~300 lines of
tests, README paragraph.
