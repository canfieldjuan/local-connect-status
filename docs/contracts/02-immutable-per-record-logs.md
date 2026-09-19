# Contract 02 — One immutable log set per stored evidence record

Status: **proposed** (review before any code). Slice 2 of the 2026-09-18 fix plan.
Scope: `lcstatus/verify.py` (log/JUnit naming in every local runner), `lcstatus/collect.py`
(`store_result`), tests, one README paragraph. Nothing in `rules.py`, `render.py`, `evidence.py`,
`catalogue.json`, or any product repository.

## Problem (evidence, not description)

A record's `log_path` is supposed to be the evidence behind that record. Today it is not:

- Every local runner writes its output to a name that depends only on the check and the revision:
  pytest `<check>.<sha12>.log` + `<check>.<sha12>.junit.xml` (`lcstatus/verify.py`, the `self.logs /`
  sites in `pytest`, `cargo_lib`, `accept_ew_ip` — `<check>.<ip8>-<ew8>.log` — and the PDF handoff —
  `<check>.<ew12>-<ds12>-<cc12>.log`). Each execution overwrites the previous file (`log.write_text(...)`,
  pytest's `--junitxml`).
- The store collapses an execution whose identity equals the latest record of its series
  (`lcstatus/evidence.py` `Store.add`, returns `False`), so a stored record's `log_path` names a file that
  every later identical execution rewrites. The stored record for `ip.pytest.all` at `411844569a00`
  (index 117, `fail`, `1 failed / 611 executed`, recorded 2026-09-11T00:01:58Z) points at
  `data/logs/ip.pytest.all.411844569a00.log`, which now reads `611 passed, 7 skipped` — zero occurrences
  of `1 failed`. The evidence for a failure that the dashboard still shows in history no longer exists.
- Superseded records lose their file entirely; the "latest" record's file is from a later run
  (`ew.pytest.unit` record `16327630bf1fa4e92b59114c`, summary `…81.22s`, log summary `…78.96s`).
- Footprint today: 656 files, 16 MB; the largest single file is `ip.pytest.all.*.junit.xml` at 1.09 MB.
  Keeping one file set per *execution* would be ~10 MB per routine tick (72 ticks/day) — not viable.

## Observable behaviour

B1. **Per-execution names.** Every file a runner writes for one execution carries that execution's
start instant: `<check>.<revision-key>.<YYYYMMDDTHHMMSSffffffZ>.log`, and for pytest the JUnit
sibling with the same stem and `.junit.xml`. `<revision-key>` is unchanged (`sha12`; `ip8-ew8` for
the invoice handoff; `ew12-ds12-cc12` for the PDF handoff). If a name already exists the runner appends
`.1`, `.2`, … to the stem rather than overwriting.

B2. **`log_path` is exact.** A record's `log_path` names precisely the file that execution wrote, and
is `None` when nothing was written. Today's behaviour per runner is kept: pytest and the two cross-app
runners write nothing on timeout or startup failure (`log_path` absent); `cargo_lib` writes the output
gathered from the steps that did complete and names that file. Only the *name* changes.

B3. **One file set per stored record.** `collect.store_result` observes `Store.add`'s result. When the
record was **collapsed** as an identical consecutive observation, the files that execution wrote (the
log and, if present, the JUnit sibling) are removed: the stored record's own files already hold the
equivalent evidence. When the record was **appended**, its files stay. Net effect: exactly one file
set per stored evidence record, and the number of files grows with observations, not with ticks.

B4. **Legacy files stay.** Files written before this slice keep their fixed names and are never
touched or renamed. Records that point at them are historical; the README states that such a file may
hold the output of a later execution at the same revision.

B5. **Presentation unchanged.** `render.py` keeps showing the basename of `log_path`; `rules.py`
passes `log_path` through unchanged.

B6. **Cleanup is best-effort and silent to the verdict.** Failure to remove a collapsed execution's
files is written to stderr (the journal) and never changes a verdict, a record, or the exit status.

## Invariants

I1. A stored record's file set is never overwritten or removed by a later execution.
I2. No execution's files are removed while its record is stored.
I3. A name cannot collide within one collection (microsecond start instant plus `O_EXCL`-style
suffixing); two collections cannot run at once (`data/.lock`).
I4. `--render-only`, `--set-baseline`, and `scripts/record_observation.py` write no files under
`data/logs`.
I5. `data/logs` growth is bounded by stored records: a tick that stores nothing leaves the directory
with the same file count it started with.
I6. Record identity is unchanged (`log_path` is not part of it), so this slice cannot change any
label, any collapse decision, or any fingerprint.

## Failure cases

| Situation | Result |
|---|---|
| pytest / cross-app timeout or cannot start | `unavailable`, `log_path = None`, no file (as today) |
| `cargo_lib` step timeout or cannot start | `unavailable`, the output gathered so far is written under the per-execution name and named in `log_path` (as today, new name) |
| pytest ran but produced no JUnit | verdict `unknown` as today; the log is kept and named in `log_path`; no JUnit sibling |
| identical consecutive observation (collapsed) | record not stored; its log (+ JUnit) removed |
| collapsed, but removal fails | stderr warning; nothing else changes |
| name already exists at write time | suffixed name; the existing file is untouched |
| `--render-only` | no file written or removed |

## Concurrency model

Unchanged. `data/.lock` (`collect.py`) excludes concurrent collections; the compatibility flock
serialises the cross-app acceptance. Names use the execution's start instant at microsecond
resolution, which no single collection can repeat; `O_EXCL` suffixing covers the residual case.
Removal on collapse happens in the same process, after `Store.add` returned, before the next runner.

## Settling test evidence

Unit (runner tests drive the real naming and removal code; the store is the real `Store` on `tmp_path`):

1. `test_identical_executions_keep_one_log_set`: two pytest executions with identical results →
   one stored record; exactly one `.log` + one `.junit.xml` on disk; their content is the **first**
   execution's; the record's `log_path` names that file.
2. `test_changed_result_keeps_both_log_sets`: pass then fail → two records, two file sets, the first
   untouched byte for byte.
3. `test_collapsed_execution_removal_failure_is_only_a_warning`: removal raises → warning on stderr,
   verdict/exit unchanged, store unchanged.
4. `test_timeout_and_startup_failure_name_only_what_was_written`: pytest and cross-app timeout /
   `OSError` → `log_path is None`, `data/logs` unchanged; `cargo_lib` timeout → exactly one new file
   holding the gathered output, named in `log_path`.
5. `test_legacy_fixed_name_files_are_never_touched`: a pre-existing `<check>.<sha12>.log` survives an
   execution of the same check at the same revision, and the new record names a different file.
6. `test_render_only_writes_and_removes_nothing`: `--render-only` on a store with records leaves
   `data/logs` identical.
7. `test_log_name_collision_is_suffixed`: a file already at the computed name is not overwritten.

Live (after merge and fast-forward): the next routine tick, which stores no new records (all
observations identical), leaves `ls data/logs | wc -l` unchanged and appends nothing; the first tick
after a product head moves adds exactly one file set per new record, and every new record's `log_path`
exists and contains that tick's output.

## Decisions

D1. **Remove on collapse rather than write-then-rename or write-only-if-new.** The verdict is not
known until the process has run, so the file must exist first; removing it when the store says the
observation is redundant is the only order that keeps "one set per stored record" exact.
D2. **Do not migrate legacy files.** Their content is already not authoritative; renaming or deleting
them would destroy what evidence remains. Honesty over tidiness: document it.
D3. **Start instant in the name, not the record id.** The id is only final after `Store.add` (re-keying
on recovery); the name must be known before the process starts so the runner can hand it to pytest.
D4. **Out of scope:** the acceptance log's `watcher commit <status-repo HEAD>` line is printed by the
product script (`git -C /tmp/watcher-main`) against an archive tree with no `.git`; it is cosmetic and
belongs to that script.

## Estimated diff

~90 lines in `lcstatus/verify.py` (one `run_files(check_id, key)` helper used by every local runner),
~25 lines in `lcstatus/collect.py`, ~150 lines of tests, one README paragraph.
