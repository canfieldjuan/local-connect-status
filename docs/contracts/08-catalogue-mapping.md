# Contract 08 — Every dependency pattern names code that exists, and one writer at a time

Status: **accepted 2026-09-24** (operator: D1 "1. remove it", D2 "2. is good with me"; rev 2 adds
`lcstatus/sources.py` to the scope for the file listing B3 reads, before any code; rev 3, after independent
review of the implementation, puts both file listings in one path form (B3a) and holds both lock handles
for the whole write (I5)). Slice 8 of the 2026-09-18 fix plan.
Scope: `catalogue.json` (task `depends_on` lists; D1 may remove one check), `lcstatus/catalogue.py`
(`depends_on` shape validation), `lcstatus/change.py` (one pattern-coverage function beside `assess`),
`lcstatus/sources.py` (one `Mirrors` method that lists the files at a revision), `lcstatus/collect.py` (per-tick
mapping check, state), `lcstatus/render.py` (one banner, one payload key),
`lcstatus/evidence.py` (one lock helper), `scripts/record_observation.py` (takes the lock), tests, README.
No rules change, no fingerprint change, no product repository.

## Problem (evidence, not description)

- **12 dependency patterns match no file.** Each task's `depends_on` says which paths, per repository, the
  task depends on. `change.assess` (`lcstatus/change.py:54-67`) matches a new revision's changed files
  against those patterns. It names the affected tasks and lists the files no task claims as "unmapped files
  need assessment". Checked 2026-09-24 against each repository's current head in the collector's own
  mirrors, with the same matcher (`change._glob_match`, `change.py:24`), 12 patterns match nothing:

  | task | repository | dead pattern | why |
  |---|---|---|---|
  | ew.meeting_suggestions_and_confirmed_write | eom-email-watcher | `src/eom_email_watcher/entitlement.py` | moved out (EW #171, 7e54ba3) |
  | connect.handoff_pdf_to_summarizer | eom-email-watcher | `src/eom_email_watcher/connect.py`, `…/entitlement.py` | moved out (EW #171) |
  | connect.handoff_bill_to_ledger | eom-email-watcher | `src/eom_email_watcher/connect.py`, `…/entitlement.py` | moved out (EW #171) |
  | release.local_connect_bundle | eom-email-watcher | `src/eom_email_watcher/entitlement.py` | moved out (EW #171) |
  | release.local_connect_bundle | eom-email-watcher | `src/eom_email_watcher/connect/**` | never existed (0 commits touch it) |
  | release.local_connect_bundle | document-summarizer | `src-tauri/src/bin/connect_provider.rs`, `src-tauri/src/entitlement.rs` | never existed |
  | release.local_connect_bundle | connect-contracts | `contracts/**`, `docs/**`, `connect_windows.py` | never existed |

  EW #171 moved the Connect host core (`connect.py`, `entitlement.py`, `connect_windows.py`, and the eight
  `automate/` modules the 2026-09-18 audit found unmapped) into `src/connect_automate/`. EW #173
  (f6924c8) then made it an external package, `connect-automate`. EW pins that package to a full commit
  SHA in `pyproject.toml:8` and in `uv.lock` (`source = { git = "…connect-automate?rev=a3e2667…" }`). The
  Email Watcher revision therefore fixes the Connect code it runs, and those two files are where a change
  to that code shows up. The document-summarizer Connect code is `src-tauri/src/connect/*.rs` (8 files,
  already claimed by `src-tauri/src/connect/**`). The connect-contracts repository holds `schemas/`,
  `fixtures/`, `entitlements/`, `adr/`, `plans/` and `tools/`.
- **Nothing reports it.** `catalogue.load` (`lcstatus/catalogue.py:49-121`) validates checks, tasks and
  conditions, but never `depends_on`. A pattern with a typo, one naming a path that never existed, or one
  left behind by a move matches nothing, and the only symptom is a change that names no affected task.
- **The observation script writes without the lock.** `scripts/record_observation.py` loads the catalogue
  (`:107`) and the store (`:143`) and appends (`:144`) without taking `data/.lock`, which every collector
  run holds (`lcstatus/collect.py:153-162`). It builds its `Store` from a file the collector may be
  appending to, and it reads mirrors (`:118`) the collector may be fetching. Every other writer of `data/`
  serialises on that lock (contract 06 B3).
- **What depends_on does not do.** It feeds only `change.assess`. No fingerprint hashes it: check
  fingerprints hash the check, condition fingerprints hash the condition (`evidence.py:87-94`), and
  `repo_config` hashes the `repos` entries (`verify.py:504-507`). No label reads it. So the catalogue
  edits below change which tasks a future change names, and nothing else.

## Observable behaviour

B1. **Catalogue mapping.** The `depends_on` lists change as follows, and no other `depends_on` entry
changes:

| task | repository | remove | add |
|---|---|---|---|
| ew.meeting_suggestions_and_confirmed_write | eom-email-watcher | `src/eom_email_watcher/entitlement.py` | `pyproject.toml`, `uv.lock` |
| connect.handoff_pdf_to_summarizer | eom-email-watcher | `…/connect.py`, `…/entitlement.py` | `pyproject.toml`, `uv.lock` |
| connect.handoff_bill_to_ledger | eom-email-watcher | `…/connect.py`, `…/entitlement.py` | `pyproject.toml`, `uv.lock` |
| release.local_connect_bundle | eom-email-watcher | `src/eom_email_watcher/connect/**`, `…/entitlement.py` | `pyproject.toml`, `uv.lock` |
| release.local_connect_bundle | document-summarizer | `src-tauri/src/bin/connect_provider.rs`, `src-tauri/src/entitlement.rs` | — (covered by `src-tauri/src/connect/**`) |
| release.local_connect_bundle | connect-contracts | `contracts/**`, `docs/**`, `connect_windows.py` | `schemas/**`, `fixtures/**`, `adr/**` |
| automate.unattended_pdf_summary | eom-email-watcher | — | `src/eom_email_watcher/automation/**`, `pyproject.toml`, `uv.lock` |
| automate.invoice_intake_and_digest | eom-email-watcher | — | `src/eom_email_watcher/automation/**`, `pyproject.toml`, `uv.lock` |

The two automate rows close the audit's "automate/ unmapped" finding under the code's current location:
the rule engine is `connect_automate.automate` (reached through the pin), and the Email Watcher's own
rules are `src/eom_email_watcher/automation/`. The remaining unclaimed files (for example
`src/eom_email_watcher/mime.py`) are not re-mapped here. When they change, the change record lists them as
"unmapped files need assessment", which is how that surface is designed to work.

B2. **Shape validation.** `catalogue.load` rejects, through its existing "catalogue invalid" error:
`depends_on` that is not a list; an item that is not an object; an item whose `repo` is not a catalogue
repository; an item whose `paths` is not a non-empty list of distinct non-empty strings; and two items for
the same repository in one task. A collector started on such a catalogue fails to start, as it does
today for every other catalogue error.

B3. **Mapping check, every tick.** After the observation phase, for every repository whose head was
observed this tick, the collector lists the files at that head from its own mirror (`git ls-tree -r
--name-only <sha>`, in the path form of B3a). For each task `depends_on` pattern naming that repository, a **gap** is recorded
when no listed file matches. The check uses `change._glob_match`, the matcher `assess` uses, called
through one new function in `change.py`. A repository whose head was not observed this tick is
**unchecked** ("current revision not observed this tick", the same predicate as contract 04 B7). A
repository whose file listing fails is **unchecked** with the failure's reason. The result is written to
`state.json` as `mapping = {"gaps": [{task, repo, pattern, revision}], "unchecked": [{repo, why}]}`, so
that `--render-only` re-renders it. It is not an evidence row and not a source failure. It changes no
label, no readiness and no exit code.

B3a. **One path form (rev 3).** The file listing and the change record's diff (`Mirrors.changed_files`)
print paths the same way: git's quoted form, where a path with bytes outside printable ASCII, a quote, a
backslash or a control character is printed as a C-style quoted string (`"caf\303\251.txt"`). Both
commands pass `-c core.quotePath=true`, so the operator's git configuration cannot change either form.
Rev 2's listing used `-z`, which prints raw bytes. Independent review reproduced two faults from that.
First, a file name that is not valid UTF-8 raised `UnicodeDecodeError` out of the listing, the mapping
check and the tick, so one such file in a product repository would stop every collection. Second, a
non-ASCII path appeared raw in the listing but quoted in the diff, so a pattern naming it read as covered
while `assess` could never attribute a change to it, which breaks I3. The quoted form is ASCII, so it
decodes, and it is the form `assess` receives. A catalogue pattern for such a path must be written in that
form, and the mapping check now reports one that is not.

B4. **Page.** When `gaps` is non-empty, a warning banner reads: "**Catalogue mapping: N dependency
pattern(s) match no file at the current revision.** A change to the code a pattern was meant to cover is
not attributed to its task." It lists `task — repo: pattern` for each gap. When `unchecked` is non-empty,
the same banner says which repositories were not checked and why. When both are empty, there is no
banner. `status.json` carries the same object as `catalogue_mapping`.

B5. **One writer at a time.** `record_observation.py` takes the collector's lock and waits for it the way
`--set-baseline` does: when the lock is busy it prints "waiting for the collection lock (another
collection is running) ..." and blocks. It loads the catalogue, the mirrors and the store only **after**
acquiring the lock, then writes and exits, which releases it. Both programs acquire the lock through one
helper in `lcstatus/evidence.py`, so the file name and the wait semantics cannot drift. As today, the
observation appears on the page when the next tick renders.

B6. **`ew.pytest.unit` (D1, operator: remove).** The check is **removed** from the catalogue. It serves no
condition (no task condition names it), and the Email Watcher's CI `test` job, `ew.ci.test`, runs the
whole suite (`uv run pytest --cov=…`) at the same revision and already backs three conditions
(`ew.ci_linux`, `connect.consumer_ci`, `rel.ew_primary_tests`). It is also the most expensive routine local
run: "1796 passed, 1 skipped, 1 deselected in 390.11s" on the 2026-09-24 19:10Z tick. Its stored rows
stay in the store and nothing selects them. Its entry in the frozen `CHECK_FINGERPRINT_ALIASES` table stays
too, because contract 03 never lets that table be edited.

B7. **README.** Under "How it runs": the mapping banner, what a gap means and how to fix it (edit the
catalogue pattern). Under the observation script: it waits for a running collection.

## Invariants

I1. Evidence is never rewritten; the store stays append-only.
I2. **No label changes at merge.** Verified live before merge: the branch's derivation over the live
store and state gives task and condition labels identical to the served page. The only page differences
are the new mapping banner and payload key (empty after B1), the changed `depends_on` lists in task
detail, and none from D1, because no condition reads the removed check.
I3. **One matcher, one path form.** The mapping check and `assess` call the same `_glob_match` on paths
in the same form (B3a). A pattern the check reports dead is exactly one that `assess` can never match at
that revision.
I4. **Zero gaps after B1.** Verified before merge: every `depends_on` pattern matches at least one file at
each repository's current head in the live mirrors.
I5. **One writer.** The observation script and the collector never read or append the store at the same
time. The observation script never builds its `Store` before holding the lock. Each program keeps its lock
handle referenced until it exits (rev 3): an unreferenced handle is closed at once, and the lock with it.

## Failure cases

| Situation | Result |
|---|---|
| a product repository moves or deletes files a pattern named | next tick: a gap for that task and pattern, banner on the page; labels unchanged; fixed by editing the catalogue |
| a pattern never matched anything (typo, guessed path) | the first tick after this slice lands shows it as a gap |
| `depends_on` names an undeclared repository, or `paths` is empty, not a list, or has duplicates | `catalogue.load` fails; the collector does not start (as for every other catalogue error) |
| a repository's head is not observed this tick | its patterns are "unchecked: current revision not observed this tick"; no gap is claimed |
| `git ls-tree` fails for an observed head | "unchecked" with the reason; no gap is claimed; no source failure |
| a product repository holds a file name that is not valid UTF-8, or a non-ASCII path | listed in git's quoted ASCII form (B3a); the tick continues; a pattern matches it only in that form |
| the operator sets `core.quotePath=false` | no effect: both listings force `core.quotePath=true` |
| `--render-only` | re-renders the last tick's `mapping` from `state.json` |
| state written by an earlier collector (no `mapping` key) | no banner until the next full tick |
| observation script started during a tick | prints the waiting line, blocks, then records against the catalogue and store current at that moment |
| observation script interrupted while waiting | nothing written; the lock is released when the process exits |
| routine tick fires while an observation holds the lock | exits 3 and is skipped, as today when any collection holds it (the observation holds it for seconds) |
| D1: an old `ew.pytest.unit` row in the store | kept; no condition selects it; not shown |

## Concurrency model

Unchanged for the collector: one exclusive `data/.lock`, routine ticks yield (exit 3), `--set-baseline`
and `--heavy-only` wait. The observation script joins the waiting side. The mapping check reads only the
collector's own mirrors, inside the lock, after they were fetched this tick.

## Settling test evidence

Unit:
1. `test_catalogue_rejects_malformed_depends_on`: each B2 violation, one at a time, is rejected with a
   message naming the task. The live catalogue loads.
2. `test_pattern_coverage_uses_assess_matcher`: `**`, `/**`, `fnmatch` and exact patterns are reported
   as covered or dead exactly as `assess` matches them (the same file list is run through both).
3. `test_mapping_check_gaps_and_unchecked`: with a real bare git repository built in a temporary
   directory (the real `Mirrors`, no fake), a dead pattern is a gap, a live one is not, an unobserved head is
   "unchecked", and a listing failure (a SHA absent from the mirror) is "unchecked" with a reason.
4. `test_mapping_banner_and_payload`: a gap renders the banner and `catalogue_mapping`; an empty mapping
   renders no banner; `--render-only` re-renders from state.
5. `test_observation_waits_for_the_collection_lock`: a real second process holds `data/.lock`; the
   script prints the waiting line, has written nothing while it waits, and records after the lock is
   released. Its `Store` is built after the lock is acquired: a row appended while it waited is visible
   to its collapse.
6. `test_one_lock_helper`: `collect` and `record_observation` acquire through the same helper and the
   same lock path.
6a. (rev 3) `test_listing_and_diff_print_paths_in_one_form`: a real repository with a non-ASCII path, a
   non-UTF-8 path and a plain path, with `core.quotePath=false` in the global git configuration. The
   listing never raises; every path it prints is byte-equal to the path the diff prints for the same
   file; and `uncovered_patterns` agrees with `assess` on both the raw and the quoted pattern.
6b. (rev 3) `test_*_holds_the_lock_while_it_reads_and_writes`: the lock is still held when the
   observation script builds and appends to its `Store`, and while the collector reads its sources.
   Checked by a second acquire, which must fail.
6c. (rev 3) The real-process lock test reads the processes' output with a deadline, so a regression
   fails the test instead of hanging it and leaving the processes behind.
7. `test_live_catalogue_has_no_dead_patterns` is **not** a unit test, because product repositories move.
   Instead, before merge: the branch's mapping check run read-only against the live mirrors at the live
   heads prints zero gaps (I4).

Live (read-only, before merge): the branch's derivation over the live store and state, with page labels
compared to the served page (I2), and the mapping check at the live heads (I4).
Settle (after merge): the first tick renders with no mapping banner and appends no rows beyond what that
tick's revisions would have appended anyway.

## Decisions

D1. **`ew.pytest.unit`: removed (operator's call, 2026-09-24).** The recommendation was to remove it: it proves nothing the page shows, CI runs a superset at the
same revision, and it is the largest routine cost. Mapping it would need a condition it uniquely proves,
and none exists: the local selectors (`ew.pytest.adapters`, `…notify`, `…scheduling`, `…automation`,
`…connect_v1/v2`, `…queue_retry`) already back the task conditions that need a local run.
D2. **A dead pattern is a warning, not a stop (revised from the plan).** The 2026-09-18 plan said
"exit 2 + banner". Exit 2 is the collector's "finished with source failures" code
(`collect.py`, last line of `main`), and a source failure means evidence could not be read, so the
existing banner says affected rows are not freshly verified. A dead pattern affects no evidence and no
label. Counting it as a source failure would put that false sentence on the page, and it would mark the
unit failed on every tick until someone edits the catalogue, burying real read failures. Product
repositories move files on their own schedule, and EW #171/#173 did exactly that. A condition the
catalogue cannot control belongs in its own warning. Shape errors, which the catalogue does control,
still stop the collector (B2).
D3. **The Email Watcher's pin files stand in for the extracted package.** Adding `connect-automate` as a
fifth tracked repository would give its own head and change stream. However, the released Email Watcher
runs the connect-automate commit pinned in its `pyproject.toml`/`uv.lock`, not that repository's head, so
the pin is what a change to released behaviour moves. Tracking the fifth repository is a separate
decision, if the page ever needs to show upstream connect-automate work before a pin bump.
D4. **No full re-mapping.** Only dead patterns and the audit's named automation gap are fixed. Deciding
which task every unclaimed file belongs to is a product judgement per file, and the change record's
"unmapped files need assessment" is the designed surface for what is left.

## Estimated diff

~30 lines of `catalogue.json`, ~20 in `catalogue.py`, ~15 in `change.py`, ~30 in `collect.py`, ~20 in
`render.py`, ~15 in `evidence.py`, ~10 in `record_observation.py`, ~220 lines of tests, ~8 README lines.
