# Contract 04 — The rules say only what the evidence says

Status: **accepted 2026-09-23** (rev 2 adds the collector's handling of a rejected row to B4; rev 3 corrects
B1 for single-app checks after the live proof showed six installer demos flipping; rev 4, after independent
review of the implementation, gates B3 on an observed head, guards every collector write under B4, keeps a
rejected row's files, and validates declared participants; no accepted behaviour is withdrawn). Slice 4 of the 2026-09-18 fix plan.
Scope: `lcstatus/rules.py` (currentness, admission, one new condition state), `lcstatus/catalogue.py`
(repository grammar), `lcstatus/collect.py` (release targets), `lcstatus/evidence.py` (write-time instant
validation), `lcstatus/render.py` (one label, one next action), tests, README "Status rules". No runner
behaviour changes; no product repository; no catalogue edit.

## Problem (evidence, not description)

Four places where the derived status can say something the evidence does not. None fires on today's
store; every one is a rule the code does not enforce and a later row could exploit.

- **P1 — a partial participant set counts as current.** `_matches_current` (`lcstatus/rules.py:80-86`)
  accepts a record with participants when *every participant it names* matches heads. A record naming a
  subset of the check's declared `participants` therefore reads as current. The other layers already
  refuse partial sets: runners write the declared set; the manual observation parser accepts only the exact
  catalogue set (`test_participant_parser_accepts_exact_catalogue_set_at_sha_boundary`); a *different*
  declared set changes the check fingerprint. The rules layer is the one that does not check. Live store
  (1,164 rows): 10 rows name a participant set other than their check's declared one — all from the
  two-participant era of `xapp.accept_ew_to_ip`, all already excluded by fingerprint. **What "declared"
  means is fixed by the writer**: `record_observation.py:112` takes the declared set as
  `check["participants"]` when present and otherwise `[check["repo"]]`, so a single-app manual demo row
  names exactly its own repository (9 live rows across the six `manual.*_install` checks do), and automated
  runner rows name nothing. A rule that read "declares none" as "must name none" would orphan those nine
  rows; rev 1 of this contract did, and the live proof caught it.
- **P2 — a wildcard repository can skip the repository filter.** `catalogue.py:47` accepts `"repo": "*"`
  for every runner except the issue gate; `collect.py:56-61` fans such a release check out to every
  repository; `rules.py:179` substitutes the task's `app_repo` for `"*"`, which the catalogue sets to `""`
  for the bundle task (`catalogue.py:74`); `rules.py:105` then skips the repository filter entirely when
  `check_repo` is falsy. So a `"*"` release check on the bundle task would admit `release_artifact` rows
  from any of the four repositories (rows exist for all four). Today's catalogue has **no** `"*"` check;
  the path is reachable only through a catalogue edit the validator allows. Three test files still fixture
  `"*"` (`test_rules.py:23`, `test_collect_failures.py:25,196`, `test_verify_parsing.py:810,843`).
- **P3 — an old failure that was never re-run reads "check skipped or unavailable".** The branch at
  `rules.py:150-152` ("records exist, but none at the current revision and none ever passed") returns
  `not_checked`, whose label is "check skipped or unavailable" (`render.py:29`) and whose README meaning is
  "the check was skipped, unavailable, pending or unknown". When the latest admitted record is a `fail` at
  an earlier revision, nothing was skipped: the check failed and has not run since. Live store: 0
  conditions in this branch today.
- **P4 — a naive or unparseable instant is ordered silently, not rejected.** `instant_key`
  (`evidence.py:49-59`) sorts such a value as `(0, 0.0, value)`, before every valid instant. Nothing
  validates `recorded_at` or `revision_time` when a runner row is written: `Record.__post_init__` checks
  kind, verdict and platform only; `Store.add` checks nothing. Manual observations validate their time
  (`record_observation.py`); runner rows rely on `now_iso()` and each source's `committed_at` being aware.
  Among records at the same revision, `latest(same_revision=True)` orders by `recorded_at`, so one naive
  newer row would lose to an older valid one and the wrong verdict would be "current". Live store: 0 naive
  or unparseable instants in 1,164 rows.

## Observable behaviour

B1. **Participant-set completeness.** The declared participant set of a check is `participants` when
the key is present, otherwise `{repo}` — the same reading the observation script enforces at write. A
record is admitted for a condition only if the set of repositories it names in `participants` is (a)
exactly the declared set, or (b) empty, and (b) is allowed only for a check that declares no
`participants` key (automated runner rows never name participants; their revision is the check's
repository head). A record that names a subset, a superset, another repository, or nothing for a check
that declares `participants`, is not admitted. Because it carries an attributable fingerprint, a passing
one is history under contract 03 and reads `config_changed`; it is never current and never
`changed_since`. Currentness is unchanged: a record with participants is current when every one of them
is at its head; a record without is current when the check's repository is at the record's revision.

B2. **Every check names one repository.** `catalogue.load` rejects a check whose `repo` is not a key of
`repos`; the `"*"` exception is removed for every runner. `release_targets` reads the check's one repository
and never fans out. `task_status` passes `check["repo"]` to `condition_status`; `condition_status` requires
a non-empty `check_repo` and raises `ValueError` otherwise (a programming error, not a data state). The
repository filter therefore always applies; the `if check_repo:` guard is gone. `catalogue.load` stops
attaching `app_repo` to tasks; nothing reads it. When a check carries a `participants` key, `catalogue.load`
requires a non-empty list of distinct catalogue repositories that includes the check's own repository
(the shape every writer assumes); anything else fails loudly.

B3. **`stale_failure`.** A condition whose admitted records exist, none at the current revision, none ever
passing, and whose latest admitted record (ordered as `latest()` orders) has verdict `fail`, is in state
`stale_failure`: label "failed at an earlier revision, not re-run"; `current` is `None`; `last_proven` is
`None`; the failing record is exposed as `last_result`. Task freshness treats it as `not_checked` (a check
must run; nothing is known about the current revision) and platform rows read `not_checked`. Next action
"Re-run at current code (<check>): <proves>", ranked after `changed_since` and before `config_changed`. It
is never proving. The same branch with a latest record of skip / unavailable / pending / unknown stays
`not_checked`, as today. **Precondition: the head is known.** `stale_failure` is asserted only when the
check's repository head — every declared participant's head, for a cross-app record — was observed this
tick. When a head is unknown, nothing is known about revision order, so the branch stays `not_checked`
as today rather than claiming "an earlier revision" the evidence cannot place.

B4. **Write-time instant validation.** `Store.add` raises `ValueError` and appends nothing when the
record's `recorded_at` is not an aware ISO-8601 instant, or when `revision_time` is present and is not
one. Aware instants with any offset are accepted. Loading an existing store stays tolerant (`instant_key`'s
ordered fallback is unchanged), so a store written by an older collector still renders; the invariant is
that this collector never writes such a row. `record_observation.py` keeps its own earlier validation.
The collector's `store_result` turns that `ValueError` into a `collection_failure` record for the same
repository (kind `collection_failure`, verdict `unavailable`, `revision_time` omitted, summary naming the
field and value) and continues the run, so one bad source timestamp is shown as a source failure on the
page rather than aborting the tick and leaving the dashboard silently stale. The rejected row itself is
never written. Every store write in the collector — runner rows, revision rows, change rows and the
collector's own failure rows — goes through that one guarded helper, so the promise holds for all of them,
not only for runner rows. A rejected row's execution files, when it has any, are kept and referenced from
the failure row's `log_path`: the log is the one artifact that still says what the run did.

B5. **README "Status rules"** states each of the above in one sentence: a cross-app record must name
exactly the declared participants; every check names one repository; an old failure that has not been
re-run reads "failed at an earlier revision, not re-run"; the store refuses a row whose instants are not
timezone-aware.

## Invariants

I1. Evidence is never rewritten; the store stays append-only; no stored id moves.
I2. **No condition changes label at merge**: the 10 partial-set rows are excluded today by fingerprint and
stay excluded (now by B1 as well); no condition is in the P3 branch; no naive instant is stored; no `"*"`
check exists. Verified on the live store (settling evidence).
I3. Every row the collector's runners write passes B4 by construction: `now_iso()` is aware, and each
source's `committed_at` is aware (git `%cI`, GitHub API `Z`). Verified by re-validating every live row.
I4. `stale_failure` is visible but never proving and never counts as current.
I5. The catalogue is the only place a check's repository is decided; the rules never substitute one.

## Failure cases

| Situation | Result |
|---|---|
| cross-app record names a subset of the declared participants, all at head | not admitted; if it passed, `config_changed` history |
| cross-app record names an extra repository | not admitted; same |
| cross-app record names no participants at all | not admitted (a demo of one app cannot prove two) |
| single-app manual demo names exactly its own repository | admitted (the writer's declared set) |
| single-app check, record names another repository | not admitted |
| automated runner row, no participants | admitted, as today |
| exact declared set, one participant behind its head | admitted, `changed_since` (as today) |
| catalogue check with `"repo": "*"` or an unlisted repo | `catalogue.load` fails loudly, listing the check |
| `condition_status` called with an empty `check_repo` | `ValueError` |
| only a `fail` at an earlier revision, never re-run, head observed | `stale_failure`; next action "Re-run at current code" |
| only a `fail`, and the repository's (or a participant's) head was not observed this tick | `not_checked`, as today |
| `stale_failure` on an issue gate or release lookup | gate `unavailable` / release not met; readiness fails closed; never "ready for release" |
| check declares `participants: []`, a non-list, a duplicate, an unlisted repo, or omits its own | `catalogue.load` fails loudly |
| revision or change row with a naive `committed_at` | reported as a `collection_failure` for that repository; the tick continues |
| only a `skip`/`unavailable` at an earlier revision | `not_checked`, as today |
| a `fail` at an earlier revision and a `pass` at an even earlier one | `changed_since` (a pass exists), as today |
| runner row with naive `recorded_at`, or naive `revision_time` | `Store.add` raises; nothing appended; the collector records a `collection_failure` for that repository and continues |
| runner row with `recorded_at` in another offset (`+02:00`) | stored |
| store file already containing a naive instant (older collector) | loads and renders; ordered by the existing fallback |

## Concurrency model

None new: pure functions over the loaded store; the write-time check runs inside `Store.add` before the
append, under the collector's existing lock.

## Settling test evidence

Unit:
1. `test_participant_set_must_equal_the_declared_set`: for a check that declares participants, subset,
   superset, extra-repo and empty-set rows are never admitted and never current, the exact set at head is
   `satisfied`, and a passing subset row alone reads `config_changed`; for a check that declares none, a
   row naming exactly its own repository and a row naming nothing are both admitted, a row naming another
   repository is not.
2. `test_catalogue_rejects_wildcard_and_unlisted_repositories` and
   `test_release_targets_read_the_one_named_repository`; the three `"*"` test fixtures move to named
   repositories and the tests that used them still assert the same behaviour.
3. `test_condition_status_requires_a_repository`: empty `check_repo` raises `ValueError`.
4. `test_old_failure_never_rerun_reads_stale_failure`: fail-only history at an older revision →
   `stale_failure`, label, next action, task freshness `not_checked`, platform `not_checked`, maturity
   `planned`; a skip-only history stays `not_checked`; a pass anywhere in history stays `changed_since`;
   a fail at the current revision stays `check_failed`. `test_stale_failure_needs_a_known_head`: the same
   fail with the repository (or one participant) absent from heads stays `not_checked`.
   `test_stale_failure_gate_and_release_fail_closed`: an issue gate and a release lookup in
   `stale_failure` leave the gate `unavailable`, maturity below "ready for release", next action the
   re-run. `test_report_and_dashboard_show_the_stale_failure_record`: the markdown report's evidence
   column and the dashboard script fall back to `last_result`.
5. `test_store_rejects_non_aware_instants_at_write`: naive `recorded_at`, naive `revision_time`, and an
   unparseable string each raise and append nothing (file bytes unchanged); an aware `+02:00` instant is
   stored and ordered by the rules as later than a `Z` instant that precedes it;
   `test_collector_records_a_rejected_row_as_a_source_failure`: `store_result` with such a row appends one
   `collection_failure` carrying the row's `log_path`, keeps the files, and appends no evidence row;
   `test_collector_guards_every_store_write`: every `store.add` in `collect.py` goes through the guarded
   helper (asserted on the source), and a revision row with a naive `committed_at` becomes a
   `collection_failure`. `test_catalogue_validates_declared_participants` (B2).
6. `test_every_snapshot_row_passes_write_time_validation` (read-only over the repository's
   `data/records.jsonl`).
7. `test_snapshot_admission_is_unchanged_by_participant_completeness` on the repository's checked-in
   store snapshot. **Known limit**: that snapshot is the 151-row legacy prefix from PR #1 and
   holds one installed demo, so it cannot see the single-app demo shape; the live proof below is the gate
   that can, and it is run against the live store, not the snapshot.

Live (branch code against the live store, read-only, before merge): per-condition state, label and
platform, and per-task maturity, freshness, platforms and issue gate are identical to the served page
(I2); the 10 partial-set rows are excluded under both rules; 0 rows fail B4 (I3). After merge and
fast-forward: the first tick keeps every label; no new row for an unchanged result.

## Decisions

D1. **Partial participant sets are not admitted, rather than admitted-but-never-current.** A row whose
participant set disagrees with its check's declared set was not produced by the current configuration;
treating it as history under contract 03 (`config_changed`) is the truthful reading and reuses an existing
state instead of adding one.
D2. **Remove `"*"` rather than fix the falsy guard.** No catalogue check uses it, the release runner
already records per repository, and a check that names no repository has no honest place in a per-app
dashboard. Removing the grammar removes the guard and the `app_repo` substitution with it.
D3. **`stale_failure` is a condition state, not a task freshness.** The task-level question is "what is
known about the current revision", and the answer is nothing, which `not_checked` already says. The
condition-level label is where the lie was.
D4. **Validate at the write boundary, tolerate at load.** The store is append-only and shared with older
rows; refusing to load would take the dashboard down for a row this code never wrote. Refusing to write is
what keeps the ordering fallback from ever being exercised by this collector. A raise, not a silent
normalisation, because a naive instant from a source is a source bug worth seeing.

## Estimated diff

~30 lines in `rules.py`, ~10 in `catalogue.py`, ~5 in `collect.py`, ~20 in `evidence.py`, ~10 in
`render.py`, three test fixtures moved off `"*"`, ~220 lines of new tests, README paragraph.
