# Contract 10 — What is installed is a recorded promotion, and the dashboard shows it

Status: **accepted 2026-10-13, revision 1** (operator, 2026-10-13: accepted, with a lag limit of seven days).
Both operator decisions are made: the two-tier cadence is accepted, and the lag limit is seven days.

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
  schemas, read from the installed package's lock data.

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
