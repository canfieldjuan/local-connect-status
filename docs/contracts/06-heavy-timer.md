# Contract 06 — Heavy checks run on their own nightly timer, isolated like the routine ones

Status: **proposed** (review before any code). Slice 6 of the 2026-09-18 fix plan.
Scope: `systemd/` (one new timer and service, `install.sh`), `lcstatus/collect.py` (one selection flag),
`lcstatus/verify.py` (`Runner.accept_ew_ds` isolation, reusing slice 1's helpers), tests, README "Heavy
checks". No rules or render change, no catalogue edit, no product repository.

## Problem (evidence, not description)

- Two catalogue checks are `heavy` — `ds.cargo.lib` (Document Summarizer's Rust suite) and
  `xapp.accept_ew_to_ds` (the exact-tree Email Watcher → Document Summarizer PDF handoff) — and the
  collector skips them unless invoked with `--heavy` (`lcstatus/collect.py:266-270`). Nothing invokes it:
  the only timer runs the routine collector (`systemd/local-connect-status.timer`, every 20 minutes).
  Four conditions therefore read **no evidence** permanently: `ds.local_lib`,
  `connect.pdf_handoff_exact_revisions`, `rel.bundle_pdf_contract`, `rel.bundle_entitlement_ds` — two of
  them on the bundle's release gate.
- Live store (2026-09-23): `ds.cargo.lib` has exactly one row, a manual run at 06:27:56Z today (551
  passed); `xapp.accept_ew_to_ds` has none, ever.
- A `--heavy` run today also re-runs every routine check, and holds the collector's exclusive lock
  (`collect.py:139-145`, `LOCK_NB` for every run) for the whole of it. The heavy runners' own timeouts sum
  to 6,600 s (`cargo test` 3,600; contracts tests 1,200; the proof 1,800). Every routine tick in that window
  exits 3 ("another collection is running") and the page is not re-rendered.
- `Runner.accept_ew_ds` (`lcstatus/verify.py:857`) runs the Email Watcher's real cross-process proof under
  `xvfb` (`:943`) with the operator's real `HOME` and inherited `XDG_*` roots: the engine under test can read
  the operator's configuration and write its own database into the operator's data directories. Slice 1
  gave the invoice handoff an isolated home with its own XDG roots (`accept_ew_ip`, `:751`); the PDF
  handoff never received it. The contracts test step invokes a bare `python` (`:910`), whichever one
  `PATH` resolves inside the sanitised environment, while every other step pins an interpreter.

## Observable behaviour

B1. **A nightly heavy timer.** `systemd/local-connect-status-heavy.timer` fires
`local-connect-status-heavy.service` once a day (`OnCalendar`, hour per D1, `RandomizedDelaySec=10min`,
`Persistent=true`). The service runs `python3 -m lcstatus.collect --heavy-only` with `Nice=19`,
`IOSchedulingClass=idle`, `CPUWeight=20`, and `TimeoutStartSec=3h` (the runners bound themselves; the
unit bounds the whole). `install.sh` installs and enables both timers and removes both on `--uninstall`.

B2. **`--heavy-only` selects the heavy checks and nothing else local.** With the flag, `wanted()` is true
exactly for checks with `heavy: true`; CI, release and issue-gate reads still happen (they are cheap and
keep the page truthful about the revisions the heavy rows are about). `--heavy` keeps today's meaning
(everything). `--heavy-only` and `--checks` together is an error.

B3. **The lock is shared and the routine tick yields.** The heavy run takes the same exclusive lock; a
routine tick that finds it held exits 3 as today and is simply skipped — never queued, never interleaved.
The page's `last_run_at` says when it was last rendered, so the staleness during the nightly window is
visible, not hidden. (D2 explains why the lock is not narrowed.)

B4. **The PDF handoff is isolated exactly like the invoice handoff.** `accept_ew_ds` builds a compatibility
home under the collector's cache with `Desktop/` links to the exact trees, creates the four XDG roots
inside it, and runs every step with `HOME` and `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_CACHE_HOME`/
`XDG_STATE_HOME` pointing into it, using the same helpers slice 1 wrote. It stages **no** licence: the
proof uses the contracts' test keyring and fixture entitlements by explicit argument, as today. The home
is removed on every exit path. The contracts test step runs with the watcher venv's interpreter, not a
bare `python`.

B5. **Evidence shape is unchanged.** The heavy rows carry the same kinds, checks, participants and
fingerprints as before; only their producer changes. Rules and render need nothing.

B6. **README "Heavy checks"** says they run nightly on their own timer at low priority, that a routine tick
overlapping the window is skipped and shows as the page's last-run time, and that the PDF handoff runs in
an isolated home.

## Invariants

I1. Evidence is never rewritten; the store stays append-only; one writer at a time (B3).
I2. **No label changes at merge**: the timer is inert until its first fire; the first fire turns four
"no evidence" conditions into real states, which is the point, and touches nothing else.
I3. A heavy run never reads or writes the operator's home (B4); verified by the same environment-capture
tests slice 1 uses for the invoice handoff.
I4. A routine tick and a heavy run never interleave (B3).

## Failure cases

| Situation | Result |
|---|---|
| routine tick fires during the heavy window | exits 3, skipped; next routine tick renders normally |
| heavy run overruns 3 h | systemd stops it; runner timeouts are 1,800–3,600 s each so this means a hung step; the next night retries; no partial row is written (each row is written after its step) |
| `--heavy-only` with `--checks` | argument error, exit 2 |
| machine asleep at the hour | `Persistent=true` fires on wake |
| xvfb, cargo or npm missing | `unavailable` rows, as today |
| PDF handoff step writes to `HOME` | it writes into the compatibility home, which is removed afterwards |

## Concurrency model

Two timers, one exclusive lock, non-blocking acquisition: the routine tick yields to a running heavy
run and vice versa. Mirrors and extracted trees are touched by one process at a time as a consequence.

## Settling test evidence

Unit:
1. `test_heavy_only_selects_exactly_the_heavy_checks`: `wanted()` under `--heavy-only` is true for the two
   heavy checks and false for every other local runner; CI/release/gate reads still dispatch; `--heavy`
   still selects everything; `--heavy-only --checks` is rejected.
2. `test_pdf_handoff_runs_in_an_isolated_home`: captured environment of every step has `HOME` and the four
   XDG roots inside the compatibility home, inherited `XDG_CONFIG_HOME` does not leak, nothing is staged
   under `.config/local-connect`, and the home is removed on pass, fail, timeout and OSError.
3. `test_pdf_handoff_pins_the_contracts_interpreter`: the contracts step's argv[0] is the watcher venv's
   python.
4. `test_heavy_units_are_installed_and_bounded`: the unit files parse; the heavy service carries
   `Nice=19`, `IOSchedulingClass=idle`, `TimeoutStartSec=3h`, `--heavy-only`; the timer is daily and
   persistent; `install.sh` names both timers in both directions.

Live (after merge and fast-forward): `install.sh` re-run; `systemctl --user list-timers` shows the heavy
timer; one manual `systemctl --user start local-connect-status-heavy.service` at an hour of the operator's
choosing (D3) produces one `ds.cargo.lib` row and one `xapp.accept_ew_to_ds` row at the current heads, the
four conditions leave "no evidence", and the routine tick that follows renders normally.

## Decisions

D1. **Hour: 03:30 local by default** — operator's call. It sits after the machine's usual idle start and
before the working day; `RandomizedDelaySec=10min` avoids a fixed collision with anything else nightly.
D2. **The lock is not narrowed to the write.** Running heavy checks outside the lock would let a routine
tick fetch into the same mirror and extract the same tree while the heavy run reads them; the store and
`state.json` are not the only shared state. One writer at a time is the property every earlier slice
relies on; a skipped routine tick once a night, visible as the page's last-run time, is the cheaper truth.
D3. **The first heavy run is started by hand, not by merging.** It is an hour of CPU at idle priority on
the operator's machine; the operator picks the moment.
D4. **No licence is staged for the PDF handoff.** Unlike the invoice handoff, the proof takes the test
keyring and fixture entitlements by argument; staging the operator's licence would test the wrong thing.

## Estimated diff

~40 lines of systemd units and `install.sh`, ~15 in `collect.py`, ~40 in `verify.py` (reuse of slice 1's
helpers), ~150 lines of tests, README paragraph.
