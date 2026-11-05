# Contract 10 — What is installed is a recorded promotion, and the dashboard shows it

Status: **accepted 2026-10-13, revision 1** (operator, 2026-10-13: accepted, with a lag limit of seven days).
Both operator decisions are made: the two-tier cadence is accepted, and the lag limit is seven days.

The install-gap amendment was accepted at `6b50df3`; the collector-proof revision below is
**proposed and requires re-acceptance**. Earlier acceptance does not cover this revision.
Contract review, recorder implementation and LCS cutover remain separate operator gates.
This proposal authorizes no live observation, gap, smoke or promotion write.

Contract 09 and `docs/RELEASE_CONTRACT.md` define the *public* release, which is a GitHub release
with installers. They say nothing about what runs on the operator's own machine. This contract adds
that missing tier. Contract 09 is not changed.

## Problem (evidence, not description)

On 2026-10-13 the operator asked where merged work actually lives. Here is what the installed state
showed that day.

| App | Installed build (proven by binary sha256) | Installed | First-parent PR merges on main since |
|---|---|---|---|
| Invoice Processor | `c698339`: `/usr/bin/invoice-processor-engine` is byte-identical to `codex-evidence/invoice-processor/coi-lifecycle-installed-20261011/invoice-processor-c698339.deb` | 2026-10-11 | #144, #150, #152, #153 |
| Email Watcher | `d36d6c6`: `/usr/bin/eom-mail-engine` is byte-identical to `codex-evidence/eom-email-watcher/paired-installed-20261009/paired-watcher-d36d6c6.deb` | 2026-10-09 | #217, #231, #239, #240, #241, #242, #244, #245 |
| Document Summarizer | **unknown**: the package records no revision | 2026-09-23 | not determinable |
| Document OCR | **unknown** | 2026-09-21 | not determinable |

- **Nothing installs main.** Merges change source code only. A fix is invisible to the operator until
  a person or a qualification run installs a package built from it.
- **The installed builds are side effects of qualification runs, not chosen releases.** The only record
  of each one is a file inside a Codex evidence folder, for example
  `coi-lifecycle-installed-20261011/final-installed-state.json` (`source_commit: c698339…`). Nothing
  collects these records, so the dashboard cannot show what is installed.
- **Nothing reports drift.** No condition notices that eight merged Email Watcher PRs are not installed,
  or that a qualification run left a test build in place.
- **The status checkout is itself stale.** The dashboard service runs from a `~/Desktop/local-connect-status`
  checkout at `4c1d7c5` (2026-09-24), which is behind `origin/main` `4aaf8a4`. The tool that should
  report drift has drifted.

## Decision: promote as proven, publish when all proven

There are two tiers, with different cadences:

1. **Promotion (this machine): continuous, per app.** As soon as a main revision passes the promotion
   gate (P3), install it and record it. Small promotions can be attributed and rolled back.
   "Promote after everything is proven" lets the gap grow without bound; Email Watcher is eight PRs
   behind already.
2. **Public release: unchanged, after everything is proven.** This is Contract 09 and RELEASE_CONTRACT:
   the milestone is clear, every installed demonstration is done, and the artifacts are published.

**Exception, coupled sets.** A revision that changes a cross-app interface is promoted together with
its partners, as one set. Cross-app interfaces are the Connect protocol, SDK pins and `connect-contracts`
schemas. The set never goes in one app at a time (P5).

## Behaviour

**P1 — A promotion record is the only accepted install.**
- Each install of an app build is recorded once, through `scripts/record_observation.py`, as a
  `promotion` observation with:
  - app;
  - repository;
  - full revision, which must be on that repository's main first-parent history;
  - package sha256;
  - sha256 of every installed program the catalogue lists for the app;
  - the gate records P3 relied on, by record id;
  - platform;
  - rollback package path and sha256 (the previously promoted package, kept);
  - `--observed-by`;
  - time.
- The recorder hashes the installed programs itself. It refuses when:
  - the hashes do not match the package;
  - the revision is not on main;
  - a gate record is missing or is for another revision.

**P2 — The collector observes what is installed, read-only, every tick.**
- For each app, it hashes the installed programs named in `catalogue.json`. It never installs, removes
  or starts anything.
- It labels each app by matching those hashes to promotion records:
  - `promoted <revision>`: the hashes match a record;
  - `unrecorded install`: they match none. **Red.**
  - `not installed`.
- A qualification run that leaves its test build installed therefore turns the app red on the next tick.
  That red is the enforcement: it is visible, persists, and needs no one to remember.

**P3 — What "proven" means for promotion.** A revision may be promoted only when:
- it is on main;
- its exact-revision CI (the app row's automated checks) is green;
- every standing gate the app's catalogue row names for the touched paths has a passing record at that
  revision (e.g. invoice-processor CERTIFICATE-EXTRACT §8.1 COI parity when its triggers are touched);
- after install, the app's installed smoke command passes on this machine:
  - `invoice-processor-engine runtime-check`;
  - Email Watcher's engine health;
  - Document Summarizer launch;
  - the exact commands are listed per app in the catalogue.

  A failed smoke reinstalls the rollback package and records no promotion.

**P4 — Lag is shown, and it ages.** For each app, the dashboard lists the first-parent merges on main
after the promoted revision that touch shipped paths. Merges that touch only docs, contracts or tests
do not count. The shipped path globs are per app in the catalogue.
- **current**: there are no such merges.
- **behind**: amber. It shows the PR numbers and the age of the oldest.
- **stale**: red. The oldest unpromoted shipped merge is older than the seven-day lag limit (operator decision 2),
  or the merge is labelled `security` or `data-loss`. Those two labels go red at once.

**P5 — Coupled sets are recorded together.**
- A promotion record lists every participant revision.
- When an app's installed revision pins another app's interface, the dashboard shows red until a single
  set record covers all participants. Pins include the SDK revision, protocol version and contract
  schemas, read through the matching P1 record's exact revision locks in the owned bare mirrors
  (accepted amendment below).

**P6 — Goals name their finish line.** A Codex goal states either "merged to main" or
"promoted `<app>` to `<revision>`". Oversight verifies a "promoted" claim only against a P1 record and a
P2 `promoted` label, never against prose. Any goal that installs a test build must end by reinstalling
the promoted package, or by recording a promotion that passes P3.

## Invariants

- The dashboard stays read-only toward the system and the product repositories. Promotion (install) is
  done by the operator, or by an agent with explicit operator authorization, through the product's own
  package. This repository only records and observes (AGENTS.md).
- A promotion record cannot be created for a build that is not actually installed, or that did not come
  from main.
- No status label comes from prose, a commit title or an evidence folder name. Labels come only from P1
  records and P2 hashes, evaluated by `lcstatus/rules.py`.
- A public release (Contract 09) remains impossible without its own conditions. A promotion is never
  release evidence by itself.

## Failure cases

| Case | Required behaviour |
|---|---|
| Installed hash matches no record | `unrecorded install`, red, showing the observed hashes |
| Package removed / program missing | `not installed`; if a promotion record exists, red ("promoted build missing") |
| Recorder given a revision not on main, mismatched hashes, or a gate from another revision | refuse; nothing written |
| Smoke fails after install | reinstall the rollback package; no record; the failure is recorded as a failed observation |
| Hashing fails (permissions, I/O) | the label is `unavailable` and blocks `current`, like the issue-gate API failure rule |
| Coupled pin mismatch across installed apps | red on every participant until one set record covers them |
| Two promotions recorded in the same tick | serialized by the existing collection lock; the last record whose hashes match wins and the other is flagged as superseded |

## Scope

- `catalogue.json`, per app:
  - installed program paths;
  - shipped path globs;
  - smoke command;
  - P2/P4/P5 conditions.
- `scripts/record_observation.py`: the `promotion` kind and its refusals.
- `lcstatus/` collector and rules: P2 hashing, P4 lag, P5 set check.
- Dashboard rendering of the four labels.
- README and tests.
- A one-time backfill: record today's two provable installs (Invoice Processor `c698339`, Email Watcher
  `d36d6c6`) from their retained packages and receipts. Document Summarizer and OCR stay
  `unrecorded install` until promoted.

Out of scope:
- product packaging and building;
- Windows (the same records with `--platform windows`, later);
- GitHub publication (Contract 09);
- updating the stale status checkout. That is an operator action, noted here because P2 only helps once
  the collector runs current code.

## Settling evidence (planned, not executed)

| Requirement | Settling test or observation |
|---|---|
| P1 refusals | Recorder tests for: hash mismatch, revision not on main, gate for another revision, missing gate. Each writes nothing. |
| P2 labels | Fake install trees for each label: `promoted`, `unrecorded install`, `not installed`, `unavailable` |
| P2 enforcement | A twin where a qualification build replaces the promoted one: the next tick goes red, and reinstalling the promoted package restores `promoted` |
| P4 lag | First-parent fixtures: a docs-only merge does not count; a shipped merge goes amber; aging past the limit goes red; a `security` label goes red at once |
| P5 sets | Mismatched pins across two installed apps: red on both; one set record clears it |
| Mutations | Each must fail a test: count docs-only merges; accept hash mismatch; label from folder name; skip the main-ancestry check |
| Backfill | The two real records are written; the live dashboard shows Invoice Processor and Email Watcher `promoted`/`behind` and the other two `unrecorded install` |

## Operator decisions (made 2026-10-13)

1. **Cadence: accepted.** Promote as proven; publish when all proven.
2. **Lag limit: seven days** for the oldest unpromoted shipped merge. `security` and `data-loss` labels go
   red at once.

## Accepted amendment — first promotion starting point

Accepted by the operator on 2026-10-13 before implementation (PR #22).

- The first promotion record per app uses the package currently installed as its own fallback,
  with its retained path and independently computed sha256. Mark the fallback explicitly as a
  starting point. There is no earlier promotion to supply a previous install.
- Every later record must name the real previous install and its retained fallback package.
  Starting-point treatment cannot replace that requirement.
- There is no exception for missing results. If the dashboard evidence store does not have the
  exact revision's passing test and required gate results, the recorder refuses and writes no
  promotion. The app stays `unrecorded install` until a properly checked build is installed.
- All main first-parent, package/program hash, installed smoke, participant and human observation
  requirements remain mandatory. A starting point is not a gate waiver or public release evidence.
- Neither collector nor recorder installs or reinstalls software; rollback remains an operator action.

## Accepted amendment — pins through the recorded revision

Accepted by the operator on 2026-10-13 before implementation (PR #22,
operator acceptance in comment 6154153726). This supersedes the pending unavailable-installed-lock
proposal.

- P5 reads SDK, protocol and schema pins from the lock files (`uv.lock`, `Cargo.lock` and the
  `connect-contracts` pins) at the exact revision named in the app's matching P1 promotion record,
  through the owned bare mirrors at that revision.
- The evidence chain is installed program hashes -> matching P1 record -> full revision -> lock
  data at that revision. The record's human observation attests the build. Current main's locks
  cannot substitute for the recorded revision's locks.
- P5 is `unavailable` only when the app has no matching P1 record. Folder names, commit titles and
  prose never provide pin evidence.
- A known disagreement is red on every coupled participant until one set record covers them.
  Product packaging, installation and the live dashboard checkout remain outside this goal.

## Proposed amendment — explicit evidence after unrecorded installs

Status: **revised proposal; exact-head oversight and operator re-acceptance pending**. The
operator accepted `6b50df39b053a4c34fa597ae43d2f698b3754da8`. Oversight found the separate
recorder PR at `1c33f1c65a67db3432d84e74eedc48ab744d56a5` NOT READY: the file-only rule rejects
the real history, while a manifest omitting its unsupported prefix passes. That implementation
stays unchanged until this revision is reviewed and re-accepted. The order is revised contract,
recorder PR revision, then separately approved LCS cutover. No live records.

### G1 — Separate recorded history from the real previous install

At baseline `3f9ee0575a1e9c965547b0cec75678d3c11a0619`, the recorder selects the latest passing
promotion at `scripts/record_observation.py:144-147`, then requires the fallback package to equal
that record's package and program hashes at `:161-172`. The first-promotion exception cannot
repair a later gap. Retrospectively promoting an intervening build also fails the current
installed-program comparison at `:157-160`.

The retained Email Watcher witness has a recorded predecessor at `9e10319`, a real previous
install at `e7887be` and a currently installed candidate at `3ed72ae`. These are three different
facts. The mismatch is retained as evidence alias
`eom-email-watcher/promotions/3ed72ae/cutover-20261104T160341Z/installed-record-chain-blocker.json`,
SHA-256 `82b8b2be4b7b1f447ecd806be97ce87052cc65cdeda2c0eed9c95677efb8879a`.
This example is not a complete inventory or authorization to record that installation history.

After acceptance, a later promotion may name a verified install gap. Preserve the last recorded
promotion as its history predecessor, but take its rollback package from the real previous
install. Never substitute the older recorded package to satisfy a comparison, manufacture
historical promotions, edit old records or treat the candidate as a new starting point.

### G2 — An explicit, content-bound gap observation

Use the existing `installed_demo` envelope, `source.type = "promotion_gap"`, and the complete
gap payload in `source.install_gap`. A closed, versioned model at the promotion evidence owner
admits it on decode and append. Pin verdict `pass`, empty condition and task IDs, empty detail,
and absent `source.check`; callers cannot attribute the gap to another check. No new dependency
or top-level record kind is needed. `Record.identity()` already includes all of `source`, so
changing a gap fact changes its identity.

The required payload is:

| Field | Required meaning |
|---|---|
| `version` | Integer literal `2`; booleans, superseded file-only version `1`, unknown versions and unknown fields refuse new gap admission |
| `app`, `repo`, `platform` | One catalogue app, its exact repository and `linux`; equal the enclosing record and candidate member |
| `candidate_revision` | Full candidate revision; equal the enclosing revision and participant |
| `last_recorded_promotion_id` | Exact latest passing promotion for this app/platform, selected under the collection lock |
| `unrecorded_installs` | Nonempty ordered events with full `revision`, aware `installed_at`, `package_sha256`, nonempty `program_hashes`, `receipts`, `installed_observation_record_ids`, and optional retained `package` path |
| `history_attested_complete` | Literal `true`; the human attests otherwise unobserved history, subject to the mandatory collector cross-check below |

The final event **is** the real previous install and must name its retained fallback package.
Earlier events may use retained package receipts without keeping every package. There is no
second editable previous-install identity. Exclude the current candidate's terminal install,
not every earlier occurrence of its bytes. Never infer chronology from Git, filenames or mtimes.

Each event's `installed_observation_record_ids` is an array of exact retained store IDs, not
paths or inline replacement records. It is nonempty for a collector-witnessed event; an empty
array requires independent hash-bound installed proof under G3. Each file receipt reference
has a retained regular-file path and full SHA-256. `receipts` may be empty only if a retained
package supplies the package/source binding and collector rows supply installed proof.
Required keys, literals, bounded ID/digest tokens and nonempty alternatives belong in the
existing closed model. Cross-record/chronological rules belong in one named promotion evidence
validator. A parallel regex or prose fallback cannot substitute for either mechanism.

The enclosing record retains the existing human observer, aware observation time, artifact
reference and artifact SHA-256. Short IDs/revisions/digests, missing/extra keys, empty identity
maps, duplicate IDs, conflicting evidence and unresolved order refuse. Referenced observations
must be retained before the gap; the recorder never manufactures a historical collector row.

**Completeness uses observed transitions, not a set of revisions.** Under the collection lock,
inspect this app/repository/platform's collector observations strictly after the predecessor's
recorded time and before the candidate observation (`source.observed_at`). This upper bound
is not a claim of the package-manager install time. Order by aware `recorded_at`; conflicting
identities at the same instant are unresolved and refuse. Identity means the whole observed
program-path/hash map, not the row's revision label.

Use the predecessor's program hashes as the start checkpoint and the independently verified
current candidate hashes as the end checkpoint. Only the leading predecessor run and terminal
candidate run are boundary states. The first sample of a terminal candidate run supplies its
boundary; if there is no such row, use the current candidate observation time. Every intermediate
observed identity run must map to a distinct manifest event, in order and within its time bounds.
Event times strictly increase inside the predecessor-to-candidate boundary interval.
Consecutive equal samples within one event may coalesce; never globally deduplicate. B -> C -> B
requires three events and distinct references for the B occurrences, even with equal revisions
or content hashes. Interior returns to predecessor bytes and earlier candidate bytes also count.

Every complete observed map must agree with its assigned event. One event can cite several
consecutive matching rows; a row cannot prove multiple events. Additional human-attested events
require separate installed proof and cannot erase/reorder witnessed transitions. Separately
witnessed events between equal samples split those samples into separate events. A tick with
missing programs or hashing errors cannot prove installation or invent an identity, and cannot
hide complete observations on either side. The human attestation covers what the collector
never saw; it cannot override contrary hashes, omitted states, order or conflicting proof.

### G3 — Bind installed observations to package evidence and the real fallback

Installed proof and package/source binding are separate facts, joined by exact program hashes.
The existing `lcstatus/promotion.py:153-199` (attribution and `installed_observation`) produces `detail.installed`
and an aware collector time. It supplies installed proof without claiming an unrecorded source revision
or package. Those still require retained package evidence.

Resolve every cited ID in the retained snapshot and require:

- kind `installed_observation`, source type `installed_program_hashes`, matching app in source
  and detail, repository and platform;
- nonempty `detail.installed.hashes`, empty `missing` and `errors`, exactly equal to the event's
  complete `program_hashes`, including historical program paths;
- aware `recorded_at` strictly after the predecessor, at or after the event's `installed_at`,
  and strictly before the next event's `installed_at`, or the candidate boundary for the final
  event. `installed_at` is a witnessed install-observation bound, not an invented exact install
  time. No observation appended after the gap may be cited retroactively;
- a retained structured package receipt or parsed package binding the same full event revision,
  package SHA-256 and program map. If the package does not encode source revision, require its
  source-bound package receipt. Expected package hashes alone do not prove installation.

An unrecorded observation may have an empty `revision`. Do not demand a promotion match,
current catalogue fingerprint, today's program paths or green overall verdict: unrelated pin
or history errors can make a row unavailable while its installed hashes are complete. Inspect
the installed fields. Missing, wrong-kind, foreign-app/repo/platform, partial, conflicting,
reused or out-of-window references refuse. Preserve the store's legitimate observation IDs:
a return transition can have a distinct retained ID despite equal content identity. Do not
require every retained ID to equal `Record.identity()` or rewrite old evidence to make it do so.

The shared promotion evidence owner checks references, hash/time binding and ordered
completeness as a **pure decision over retained records and admitted package identities**.
Recording may read/hash receipt or package files to admit those identities. Matching a retained
promotion never reopens files or live installed paths. G6 reuses this decision on the store
prefix before that promotion's publication and its original observation interval; subsequent
collector ticks cannot rewrite the historical interval or make a valid promotion stale.

For events the collector never saw, retain the closed `installed-v1` receipt option: literal
installed truth, app/repository/platform, full revision, aware install observation, package
SHA-256 and actual installed program hashes must all agree. Inventory other structured layouts
before adding an adapter. The source-only `ew-acceptance` receipt is optional supplemental human
evidence, not hash-bound installed proof. Pairing its boolean with `ew-package` cannot replace
collector references or an independent hash-bound installed receipt. A builder's `installed`
flag, free-form summary or filename cannot substitute. Unsupported, ambiguous or contradictory
proof refuses; never silently drop an unverifiable event.

For the final event, hash the actual retained fallback package and parse its regular program
payloads through the existing package reader. Package hash and program map must equal that
event's admitted package and installed proof. Missing/mismatched fallback blocks recording;
package identity is not database-restore authority or a new rollback-compatibility claim.

Use structured file/package APIs. Hash and interpret the same admitted bytes; refuse or keep
the verified open identity on replacement. Reject conflicting duplicate members and special
files. Never execute, source, evaluate or install evidence. Public references use aliases and
hashes; records and PRs contain no customer content, credentials or private configuration.

### G4 — The current candidate still passes the complete promotion gate

All existing current-build requirements remain: exact installed/package program equality,
main first-parent membership, catalogue identity, latest exact-revision CI and automated checks,
standing gates, installed smoke, human observation, participant pins and coupled-set checks.
The gap is neither a gate waiver nor a passing smoke or historical promotion. Historical
unrecorded entries need truthful install provenance; they gain no claim that they passed P3 or
came from main merely by appearing in the gap.

Compute touched paths and triggered standing gates from the last recorded promotion to the
candidate, including changes later reverted. Do not shorten that interval to the real previous
unrecorded install. Select gates through the existing admission owner; an older pass cannot
replace a later matching failure. Current program hashes and gate state are checked under the
collection lock before publication. No automatic installed smoke is executed by gap recording.

The resulting promotion member's fallback retains `previous_record` as the last recorded
promotion ID and adds `install_gap_record_id` plus `previous_install_revision`. Its package path,
package hash, previous-install revision and program identity must equal the final admitted gap
entry. App, platform, candidate and predecessor must agree throughout. A mismatch or dangling
reference refuses; callers cannot override the admitted fallback after validation.

The ordinary continuous-history path and first-promotion exception retain their existing
semantics. A known unrecorded interval uses the explicit gap even if its final install happens
to equal the recorded predecessor's bytes. A gap requires a recorded predecessor; it cannot
replace the first-promotion rules.

### G5 — Serialized append, truthful partial completion and retry

Use the existing collection lock before reading the catalogue, records or predecessor, and
hold it through all verification and appends. One recorder invocation validates every candidate
member and gap before writing any gap or promotion. A coupled set still produces one promotion
covering every required participant; one member's failure prevents all new appends.

After successful preflight, append each admitted gap observation, then the promotion referencing
the retained gap IDs, through the existing append-only store. Verify the retained identities.
Do not claim a multi-record atomic transaction: the store is atomic per record. An I/O error or
crash may leave a gap without a promotion; retain that partial truth, report failure and stop.
That orphan must not change any installed label or supply any gate.

An identical retry may explicitly select the retained gap ID. Revalidate its complete payload,
collector references/completeness, receipt identities, current installed bytes and required
gates against the newly read snapshot under the lock. If the exact
linked promotion is already the latest promotion for that app/platform, return its retained ID
without duplicating the transition; that completed transition is not a stale-predecessor error.
Otherwise the latest predecessor must still be the gap's recorded predecessor. A different
newer promotion or changed candidate, platform, history, evidence or gate input cannot reuse
the gap as a shortcut. A recorder that waited for the lock must read the new store state after
acquiring it; it cannot publish a stale plan.

### G6 — Shared interpretation and compatible evidence

Keep gap decoding and binding at one promotion admission owner, reused by recorder and rules.
Record consumers use the admitted structured identity, not raw summaries or another parser.
For a promotion with an install-gap reference, matching requires the referenced gap to exist,
decode, have a content identity matching its payload and bind that exact app/platform,
predecessor, candidate and fallback. A malformed or missing link cannot make P2 green.
The pure G3 validator also checks collector references and complete transition order against
that publication prefix, rather than trusting the write's human flag. A dangling or contradictory
installed observation cannot supply historical proof.

The gap observation itself is excluded from promotion matching, smoke, CI, standing gates,
public-release proof and coupled-set proof. It has no such check or condition attribution.
Until the qualifying promotion is retained and current installed hashes match it, P2 remains
`unrecorded install`. P4/P5 continue to use the actual promoted candidate, not an intervening
revision. No new status label is introduced.

Preserve identities and meaning of existing records, including legacy promotions without gaps.
Prove that the new envelope and linked promotion can be decoded by the previously deployed
reader without crashing, rewriting evidence or treating the gap as promotion evidence. A
rollback reader's tolerance does not authorize recording with that older recorder. Receipt and
fallback retention remains required; this amendment adds no evidence cleanup or migration.

### G7 — Work sequence and live boundary

1. This PR changes this contract and public declarative fixture vectors only. Obtain fresh
   exact-head oversight review and direct operator re-acceptance, recorded separately, before
   revising the recorder PR. Earlier acceptance at `6b50df3` does not accept this revision.
2. Implement the accepted behavior in a separate PR using isolated fixture stores and packages.
   No real observation, smoke, gap, promotion or manual edit of `records.jsonl` is authorized.
3. Obtain separate operator approval for the LCS cutover. Contract 11 automatically deploys
   main, so keep PRs unmerged while a merge could activate an unapproved LCS release; arrange
   that deployment boundary with the operator before merging to main. Do not change live units,
   pins or deployment settings to manufacture an offline merge.
4. Only after the accepted implementation is reviewed and the approved LCS cutover is complete
   may the human-run recorder write the real gap and candidate promotion. Validate the real
   retained history first; contract or code approval alone cannot supply historical evidence.

Future implementation scope is the recorder, promotion receipt/identity admission and its
shared rule consumer, operator documentation and fixture tests. Existing record envelopes,
identity and append owners are reused. No product build/install, product configuration change,
historical backfill by hand, gate relaxation, collector deployment-policy change, new dependency
or public-release claim is part of this amendment.

### G8 — Settling evidence after acceptance

All implementation tests use temporary fixture data roots, repositories, installed-file trees
and public synthetic receipts/packages. They never open the live evidence store or run a
product, smoke command, model, GPU, mail operation or LCS deployment.

[10-install-gap-fixtures.json](10-install-gap-fixtures.json) contains public declarative vectors
and expected **history-admission** results, not executable recorder tests or proof the change
works. Symbolic names expand to the file's full identity/event values; the later test harness
materializes packages/receipts in its temporary root. Synthetic collector IDs use the existing
Record/Store owner, including a return-transition ID. Other promotion gates are assumed valid
in these vectors and retain their independent tests.

| Requirement | Required fixture evidence |
|---|---|
| Truthful gap | Recorded A, unrecorded B/C, installed candidate D: gap names A and ordered B/C, fallback is verified C, linked D promotion passes; single and longer valid histories pass, including a repeated revision with separately witnessed install events |
| Collector proof | By-ID B/C observations plus package receipts pass without acceptance receipts, even with empty observed revision or unrelated pin errors; wrong ID/kind/app/repo/platform, partial hashes, hash errors, reused references, wrong bytes and early/late rows refuse |
| Package/receipt binding | Wrong package map/SHA/source refuses despite matching collector bytes; optional acceptance cannot rescue it; unseen events need complete hash-bound installed receipts |
| Ordered completeness | Omitted B, omitted middle C, omitted return B, swapped order and equal B/C sets with wrong multiplicity refuse despite human attestation; adjacent samples coalesce only within an event; B/C/B and an interior return to predecessor bytes remain distinct |
| Interval boundaries | Only leading predecessor and terminal candidate runs are boundaries; an earlier candidate or interior predecessor cannot be globally excluded; same-time conflicts and rows at predecessor/next-event boundaries cannot prove events |
| Unobserved events | Extra attested events with hash-bound installed proof pass between witnesses; unsupported unseen events refuse and cannot excuse an omitted witnessed event |
| Shape and completeness | Missing/mixed keys, version 1, unknown/boolean version, false completeness, empty proof alternatives/list/map, short hashes, duplicate/conflicting evidence and unknown order refuse before append |
| Real fallback | Missing, corrupt, wrong-revision or older A fallback refuses; valid C passes; historical program paths may differ from today's catalogue without rejection by coincidence |
| File/package boundaries | Replacement between hash and parse cannot validate different bytes; special files, duplicate/nonregular payload members and input-derived executable paths cannot bypass admission |
| Current gates | Wrong installed bytes, off-main candidate, missing/latest-failed exact gate, wrong participant or failed smoke still refuses; a gap never supplies those gates |
| Standing interval | A gate-triggering change between A and B, later reverted before D, still requires the standing gate for D |
| Binding and labels | Wrong app/platform/predecessor/candidate/fallback, stale/dangling gap or collector reference, tampered proof and orphan gap cannot make P2 green; later ticks do not change a retained interval |
| Lock race | A promotion or intervening installed observation lands while the recorder waits; it rereads under the lock and refuses stale/incomplete history without append |
| Crash and retry | Crash after gap append leaves an orphan and no promoted label; exact retry reuses retained IDs, while altered evidence or a changed predecessor refuses reuse |
| Coupled set | One invalid member writes no new gaps/promotions; an orphan cannot stand in for the required single coupled promotion |
| Compatibility | Ordinary/first promotions, legacy and return-observation IDs, old-reader decoding, the pinned no-attribution envelope and P2/P4/P5 remain correct |
| Mutations | Each must fail: use A as C fallback; accept expected hashes or source-only success as install proof; skip by-ID hash/time binding or completeness; globally deduplicate or remove every predecessor/candidate identity; skip latest predecessor; narrow standing interval; ignore gap/proof links; treat gap as smoke/promotion |

No implementation suite or mutation probe runs in this contract-only PR. Its checks are a cold
readthrough, JSON/reference validation of public vectors, `git diff --check` and proof that the
diff contains only this contract and its declarative fixture file. The later
implementation requires its focused fixture tests, applicable full-suite gate, exact-head
oversight and reconciled review findings. Operator acceptance and cutover authority remain
separate from those technical results.
