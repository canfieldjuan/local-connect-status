# Contract 11 — The dashboard keeps its own code current and its open page fresh

Status: **accepted 2026-10-13, revision 1** (operator: "i accept the contract"). The operator
accepted the proposed defaults for all four decisions at the end.

Contract 10 made the dashboard *report* how far each product app's installed build lags main. This
contract covers the dashboard itself, which has two remaining manual steps: reloading the open page,
and pulling its own code. Product apps are out of scope. Building, installing and promoting them
stays the Contract 10 / `docs/PROMOTION-LINUX.md` workflow; this repository only reports their status.

## Problem (evidence, not description)

- **The open page never refreshes.** `lcstatus/render.py` writes `site/status.json` and a static
  `site/dashboard.html` atomically on every collection. The page fetches nothing afterwards, so an
  open tab keeps showing the state from when it was loaded until someone reloads it.
- **The dashboard's own code updates only by hand.** All three units run from the operator's
  checkout (`WorkingDirectory=%h/Desktop/local-connect-status`, `systemd/*.service`). On 2026-10-13:
  - that checkout was at `4c1d7c5`, behind `origin/main`;
  - the operator had to run `git pull --ff-only` twice (to `35ad3c8`, then `d94c84f`) before Contract
    10 and its fix took effect;
  - between those pulls, a defect fixed on main (#24) kept every collection exiting 2.
- **Code and live data share one git tree.** `data/records.jsonl` and `data/state.json` are tracked
  (added in `26ccfeb`) and also rewritten by every collection. The checkout is therefore always dirty.
  A pull succeeds only while upstream never touches those paths; the first upstream change to them
  makes a fast-forward fail.
- **A stopped timer is silent.** Collection runs every 20 minutes only while
  `local-connect-status.timer` is active. If it stops, the page keeps showing its last state, with
  nothing marking it as old.

## Behaviour

**R1 — The open page refreshes itself and says how fresh it is.**
- The page polls `status.json` on the same loopback origin at the refresh interval (operator
  decision 1). When its rendered timestamp changes, it re-renders in place, keeping the selected view
  and scroll position. No full reload; no other host.
- It always shows when the data was collected ("collected N minutes ago").
- It turns that line **red, "collection stale"**, when the last collection is older than the stale
  limit (operator decision 2). A stopped timer, a hung collection or a crashed collector therefore
  becomes visible.
- If `status.json` cannot be fetched (the web service is down), the page shows "dashboard service
  unreachable" instead of the old state as if it were current.

**R2 — Code and data are separate.**
- Live state moves to a data directory outside any code tree, by default
  `~/.local/share/local-connect-status/` (records, state, logs, caches, rendered site). Code reads
  the path from configuration, never from its own tree.
- A one-time migration copies the existing `data/`, `.cache/` and `site/`, verifies each copied
  file's hash, and only then switches over. The originals are left in place until the operator
  removes them.
- The tracked `data/` files stop being written. `records.jsonl` remains the append-only store
  (Contract 02) at its new path.

**R3 — Deployment of an exact main revision, tested before use.**
- A deploy step runs at the start of each collection tick. It fetches `main` and acts only when
  main's first-parent head differs from the deployed revision and descends from it.
- To deploy a candidate it:
  1. extracts that exact revision into a new release directory
     `releases/<full-sha>/` under the data root;
  2. builds its environment from its own lock file;
  3. runs the full test suite there;
  4. **only if the suite passes**, atomically switches the `current` symlink that every unit's
     `WorkingDirectory` and interpreter use.
- A candidate that fails a step is never switched to.

**R4 — Rollback is automatic and kept.**
- The previous release directory is kept.
- If the first collection after a switch exits non-zero (other than the existing per-source
  failures), or fails to render, the deploy step switches `current` back to the previous release,
  marks the candidate failed, and does not retry that revision.
- A later main revision is still eligible.
- The operator can also roll back by command (`lcstatus.deploy --rollback`), which switches to the
  previous kept release and records it.

**R5 — Deployment is visible.**
- Every deploy attempt appends a `deployment` record: candidate revision, outcome (`deployed`,
  `tests-failed`, `env-failed`, `rolled-back`, `not-descendant`), duration, and the test summary line.
- The page shows a "Dashboard code" row: the deployed revision, main's head, and the last attempt's
  outcome.
  - **green**: deployed equals main;
  - **amber**: main is ahead and no attempt has failed yet;
  - **red**: the last attempt failed or rolled back, with its reason, until a later revision deploys.

## Invariants

- No product repository is modified. Product builds, installs and promotions are untouched;
  Contract 10 still only observes them.
- Only first-parent `main` revisions that descend from the deployed revision are deployed. No
  force-moved head, branch, tag or local edit is ever deployed.
- Nothing is deployed without its own full test suite passing in its own extracted tree and
  environment.
- `records.jsonl` stays append-only. Deploy and rollback never rewrite or delete evidence.
- The deploy step and collection hold the existing collection lock (Contract 08 B5), so they never
  overlap.
- The page talks only to its own loopback origin.

## Failure cases

| Case | Required behaviour |
|---|---|
| Candidate tests fail | No switch; a `tests-failed` record; the dashboard row red with the failing summary |
| Candidate environment fails to build | No switch; `env-failed`, red |
| First collection after a switch fails | Automatic rollback; `rolled-back`, red; that revision is not retried |
| Main is not a descendant of the deployed revision (force push) | No deploy; `not-descendant`, red |
| Disk full during extraction or environment build | The candidate is discarded; the current release keeps running; failure recorded |
| Timer stopped | The page shows "collection stale" in red once past the stale limit |
| Web service down | The page shows "dashboard service unreachable" |
| Migration hash mismatch | No switch to the new data root; the old layout keeps working; failure shown |

## Scope

- **Page:** `lcstatus/render.py`, for the refresh loop and the freshness and code rows.
- **New `lcstatus/deploy.py`:** fetch, descendant check, extract, environment, suite, switch,
  rollback, and records.
- **Paths:** the collector and units read the data root from configuration.
- **Units and installer:** `systemd/*.service` move to the `current` symlink and the data root;
  `systemd/install.sh` installs them.
- **Migration** script, README and tests.

Out of scope:
- product app builds, installs or promotions;
- deploying to any other machine;
- notifications outside the page.

## Settling evidence (planned, not executed)

| Requirement | Settling test or observation |
|---|---|
| R1 refresh | A rendered page with a changed `status.json` re-renders without a reload and keeps the selected view. A stale timestamp shows red. A failed fetch shows "unreachable". |
| R2 separation | After migration, a collection writes only under the data root; the code tree stays clean (`git status` empty). Migration refuses on a hash mismatch. |
| R3 tested deploy | Fixture repositories: a passing descendant revision deploys; a failing suite, an environment failure and a non-descendant each refuse with the right outcome and no switch. |
| R4 rollback | A candidate whose first collection fails is rolled back automatically and never retried. Manual `--rollback` restores the previous release. |
| R5 visibility | Each outcome appends exactly one `deployment` record; the page row shows green, amber or red as specified. |
| Mutations | Each must fail a test: switch before tests pass; skip the descendant check; roll back without a kept release; write data into the code tree. |
| Live | After acceptance and merge, one real deploy of a main revision on this machine. Operator-observed: the page updates itself, the code row turns green, and a deliberately failing candidate on a scratch branch is refused without touching the running release. |

## Operator decisions (accepted defaults, 2026-10-13)

1. **Refresh interval** for the open page. Proposed: every 60 seconds.
2. **Stale limit** for "collection stale". Proposed: 45 minutes (just over two collection intervals).
3. **Deploy cadence.** Proposed: check at every collection tick (every 20 minutes). The alternative is
   once an hour.
4. **Data root.** Proposed: `~/.local/share/local-connect-status/`.

## Bootstrap and attempt finalization — accepted amendment

Accepted by the operator on 2026-10-13: "I accept option A for contract 11". Oversight
relayed acceptance in PR #26 and mailbox receipt `13c58cecee304f1ea89e864ce59d39cd`.
This acceptance is recorded before implementation.

Decision:

- An empty deployment layout is permitted only for the operator's explicit first-deploy command.
  A timer tick does not silently bootstrap. The command fetches main, extracts its exact head,
  builds from that revision's lock, and runs that revision's full suite.
- With no kept release, the first collection must also finish and render successfully before
  publishing `current`. Existing per-source failures are still allowed. A bootstrap failure leaves
  `current` absent; it must never claim a rollback without a kept release.
- Extraction failures, including disk-full, are recorded as `env-failed` with the actual reason.
  A failed bootstrap collection is also `env-failed`, with the collection/render failure named.
- Each attempt gets one terminal `deployment` record: `deployed` after its first collection passes
  (or has only existing per-source failures), otherwise the applicable failure. A normal candidate
  switch can be pending while its first collection runs under the same lock; if that collection
  fails, the one terminal outcome is `rolled-back` after restoring the kept release.
- Manual rollback refuses without a kept release. It does not append a fictitious `rolled-back`
  record. All later deployment switches retain the previous release as R4 requires.

The alternative operator-supplied seed release was not adopted.

## Local-only scope — accepted amendment

The operator resolved the Vercel scope question: "retire the integration from vercel. were not
 deploying these to vercel" (message `Sentinel_7b452dacc5808191a32252117d22a533`, relayed from
 the operator conversation). Acceptance is recorded before implementation.

Retire this repository's Vercel integration and its dedicated static-build tests. The dashboard
is local-only; no legacy code-tree write exception is adopted. This adds `vercel.json` removal
and disabling this repository's remote Git deployment connection to scope. Preserve remote
project/deployment data and do not alter unrelated projects. An unavailable permission or
separate irreversible remote action is reported as a remaining blocker, not as completed retirement.

## Main lookup and failed-candidate visibility — accepted clarification (Option A)

The operator's newer decision is **A** (message `Sentinel_ae8619927c7481919c4916defb24ba03`,
relayed from the main conversation). The question explicitly described A as current-main failure
red, a newer untried revision amber, and confirmed deployed equals main green. This supersedes the
earlier relayed B decision (`0633282bf6da44118734b0cabe2c6e28`). This acceptance is recorded as its
own commit before implementing the changed semantics; the local-only/Vercel decision is unchanged.

- Fetch and lineage-read failures are typed observations, not terminal deployment attempts.
  The code row is amber, `main not observed`, with the source reason for that tick. The last
  confirmed head is historical, not proof of current main or its eligibility. Recovery to a
  successful observation is retained even when it returns to an earlier head.
- Red means the observed current main revision failed deployment and is not the deployed
  revision. A newer, not-yet-attempted main revision is amber. Confirmed deployed equals observed
  main is green. An earlier failed candidate does not impose unconditional red on a different
  current main. The row still exposes the last attempt's revision, outcome and reason as history.
  This replaces R5's original unconditional persistence until a later successful deployment.
- Candidate environment builds and full suites receive fresh per-attempt temporary data, HOME
  and XDG roots, created and removed under the deploy cache. Only actual collection receives
  the live root. A fixture candidate writes to its configured root to prove isolation; a mutation
  supplying the live root must fail that test. No real live store is used for this evidence.
- Bootstrap publication is recoverable: recovery distinguishes successful first collection
  followed by interrupted publication/terminal append from a bootstrap that never published.
  It records one truthful terminal outcome without claiming a rollback without a kept release.
- A lineage-read failure after fetch is still an observation, so the retained release can collect.
  In-place refresh preserves open installed evidence with stable app-keyed identities.

Non-blocking retry caching and environment-portability suggestions remain deferred; they are
outside this bounded implementation.
