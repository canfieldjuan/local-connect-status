# Contract 13 — Bounded retention of dashboard release trees

Status: **proposed, not accepted**, 2026-10-14. No retention code is authorized until Juan
accepts this policy and that acceptance is recorded separately before implementation.
Tracks [issue #27](https://github.com/canfieldjuan/local-connect-status/issues/27).

## Starting point and evidence

Main `9312f2f94e572c0ecb7253913373c5707ae73d63` implements Contract 11's tested self-deployment.
`Deployer.tick` keeps `current` and `previous`, journals incomplete attempts in
`data/deployment-pending.json`, and retains older extracted trees under `releases/<full-sha>/`.
It has no retention policy. `Deployer.switch` remains the sole release-switch owner.

Contract 11's accepted Option B explicitly deferred automatic deletion to a separately
accepted contract. Its earlier unaccepted cleanup proposal `254497b` grants no authority.
Oversight reported a scratch release at approximately nine MB; this is not a live measurement.
The operator's approximately fifty-release / twenty-GB-free planning trigger motivates this
work. It does not authorize a free-space watcher, emergency deletion or a live measurement.

## Proposed operator decisions

Accepting this contract accepts these defaults together:

1. Keep the two deployed pins, **current and previous**, plus the controller's executing
   release if distinct. No extra age or count window is added.
2. **Any pending deployment journal blocks cleanup entirely**, including a malformed journal.
   Recovery runs first; retention never interprets unfinished work as garbage.
3. Run cleanup at the end of a successful ordinary collection tick, under the existing lock,
   after deployment/recovery and all required rendering/finalization complete. Existing
   per-source collection failures remain allowed, exactly as Contract 11 permits.
4. Add an operator preview/apply command. Preview is the default; deletion requires `--apply`.
   Codex tests only fixtures and does not run either mode against the live root.
5. A cleanup failure is logged separately. It cannot rewrite a deployment outcome, change a
   dashboard label, cause a rollback or turn an otherwise completed collection into failure.

The fifty-release and free-space figures remain planning triggers, not runtime thresholds.
Routine cleanup removes eligible obsolete trees without waiting for disk pressure.

## R1 — One retention owner and an explicit protected set

New `lcstatus/retention.py` owns planning, protection checks, deletion and its cleanup journal.
`lcstatus/deploy.py` calls it at the one completed-tick boundary; it contains no second pruning
rule. Reuse `Paths` for runtime locations and the existing collection-lock owner.

Before any deletion, while holding that lock:

- Require an existing configured runtime layout and a valid owned `current` pin. An absent
  `previous` is allowed after initial bootstrap; an invalid pin is not silently ignored.
- Protect the exact `current` and `previous` destinations using the deployer's existing pin
  validation. Protect the executing controller's exact release when it lives under this
  runtime root's `releases/`. A source checkout outside that directory is never a target.
- Refuse cleanup while `deployment-pending.json` exists, regardless of its contents. Never
  clear, repair or finalize a deployment journal from the retention module.
- Enumerate only immediate children of the owned releases directory. An eligible obsolete
  tree has a full lowercase SHA name, is a real directory, and has controller ownership
  evidence: a matching passing `.release.json`, or an existing terminal dashboard deployment
  record naming that revision. A folder name alone is insufficient.
- Preserve unknown names, files, special entries and trees without ownership evidence, and
  report them as skipped. A SHA-named symlink or conflicting release receipt refuses the
  whole plan before deletion. Missing evidence is never manufactured.

Pinned/protected trees are never eligible, even if their receipts or names also match the
obsolete-tree criteria. Ordering is deterministic by full SHA, not filesystem timestamps.

## R2 — Timing and preservation of deployment behavior

Integrate retention after a completed ordinary `--collect` tick, including a tick that finds
main already current. A successful deployment must already have finished its first collection,
terminal render, terminal record append and pending-journal cleanup.

Do not prune during extraction, environment construction, suite execution, first collection,
publication, journal recovery, bootstrap or manual rollback. Skip the retention hook when a
tick has a deployment/lookup/recovery failure or incomplete finalization. The next successful
ordinary tick can reclaim older unprotected trees. Do not retry a failed cleanup within the
same tick.

The current controller remains protected even after a switch. A controller version that
predates this contract gains no retroactive hook: cleanup begins when the deployed controller
containing retention runs a subsequent successful ordinary tick.

Keep Contract 11's fetch, first-parent descent, exact extraction, own-lock environment and
full-suite gate, atomic switch, kept rollback and no-retry rule for rolled-back revisions
unchanged. Keep its one terminal deployment record per attempt. Retention is not deployment
or promotion evidence and does not add a verdict, record kind, schema field or page label.

## R3 — Deletion stays inside the owned releases directory

The deletion owner opens the releases directory without following a symlink and anchors
operations to that opened directory. It refuses a symlinked releases root. Validate each
candidate's type and identity again immediately before removing it; a replacement or changed
candidate stops the remaining cleanup. Use filesystem primitives that resist symlink races;
if the platform cannot provide them, refuse instead of falling back to unsafe traversal.

Never follow a top-level or nested symlink for traversal. A symlink inside an eligible tree
may be unlinked as that tree's own entry; its target is never traversed or deleted. Refuse a
nested mount/different-device boundary rather than recurse into it. A disappearance or I/O
error is reported honestly, not silently converted into a successful deletion.

Never delete or rewrite anything in the configured runtime's data, site or cache locations
(including overrides), the repository mirror, the source checkout, or a product tree.
Preserve all authoritative records and all existing environment, suite, collection and
application logs. Tracked sample/seed files embedded in an obsolete extracted source tree
remain part of that archive; Contract 11 prohibits runtime evidence writes there. Retention
removes only eligible extracted code, that tree's environment and duplicate release metadata.
Do not reclaim shared dependency
caches, scan processes, signal services, use sudo, install software or change units.

## R4 — Preview, apply and audit

The operator interface is:

```text
python -m lcstatus.retention --data-root PATH
python -m lcstatus.retention --data-root PATH --apply
```

Both modes acquire the same collection lock; contention refuses without cleanup. They do
not fetch main, deploy, collect, render, switch pins or run tests. Preview reports the protected
set, eligible deletions, skipped entries and refusal reasons. It performs no deletions and
does not append records or cleanup logs; existing lock bookkeeping is the only possible write.
Never initialize a missing runtime layout as a side effect of preview or apply.

Apply and the automatic hook use exactly the same planner and deletion owner. Before the
first deletion, create an exclusive per-attempt journal under `data/logs/retention-<id>.jsonl`,
with the protected set and candidate plan. Failure to create/write that start entry means no
deletions. Append each completed deletion and each error, followed by a terminal summary.
Existing journals are never overwritten or pruned. Do not claim an atomic all-or-nothing batch:
if removal partially succeeds and later fails, preserve the partial truth and stop the rest.
If writing a result fails after a deletion, stop further deletions and report the journal fault.

Manual apply returns nonzero on refusal or error. For the automatic hook, report the cleanup
problem to stderr and the journal where writable, preserving the completed tick's original
return and truthful deployment outcome. An invalid retention plan is not a failed product test.
No new dashboard condition or notification mechanism is added.

## Scope and live boundary

- New `lcstatus/retention.py` and a bounded integration in `lcstatus/deploy.py`.
- README operator documentation and fixture tests. Reuse the existing path, pin-validation
  and lock owners; change them only if necessary for this explicit contract, with ownership
  and compatibility evidence.
- No product repository changes, new release policy for product apps, migration, cache/log
  reclamation, new labels, units, scheduling, external integration or work on other issues.
- Never inspect, measure or prune the live release root from Codex. Never touch live units,
  service, old checkout, data or site. No actual deletion trial on the operator's installation.

After acceptance, implementation merge is a release: the current service will deploy it on
its normal tick and a later successful tick can delete obsolete live releases according to
this accepted policy. Approval must cover that automatic effect. No manual cut-over or live
prune is performed by Codex.

## Settling evidence after acceptance

Every test uses temporary data roots, fixture release directories, fixture deployment records
and fixture repositories. Never use the live root, real product installations or system files.

| Requirement | Required fixture evidence |
|---|---|
| Protection | Current, previous and executing release survive; obsolete owned trees are removed; absent previous after bootstrap is safe |
| Pending recovery | Valid and malformed pending journals block all deletion; pending candidate/previous and journal bytes remain unchanged |
| Ownership | Full SHA names alone do not authorize deletion; valid receipts or terminal dashboard records do; unknown/malformed/conflicting entries are preserved or refuse as specified |
| Paths | Symlinked releases root, SHA-named escape link, replacement race, nested external link and different-device boundary cannot delete outside sentinel bytes |
| Lock | An existing holder prevents both apply and the automatic hook from deleting; all mutation paths use the shared lock |
| Timing | A completed deployment and an unchanged-main successful tick prune; candidate build/suite/collection, failed deploy, incomplete finalization, bootstrap and rollback do not |
| Preservation | Records, existing logs, mirrors, caches, site, source and pins remain byte-for-byte unchanged; kept rollback still works |
| Preview | The exact candidate/protected plan is reported without deletion, evidence/log append or deployment; missing layout is not initialized |
| Audit/errors | Journal start precedes deletion; partial success is retained; deletion/journal failure stops later work; automatic cleanup cannot alter a real deployment outcome or trigger rollback |
| Idempotence | A later successful tick has nothing to delete after completed cleanup; failed cleanup is not retried within the same tick |
| Mutations | Removing current protection; ignoring pending; following an escape symlink; deleting without ownership proof; pruning before successful finalization — each must fail a fixture test |
| Release gate | Full suite once on the final changed head using the repository .venv; bot findings reconciled; oversight PASS on that exact head; clean local equals remote and zero unresolved threads before a merge commit |

No implementation suite or mutation gate runs at this proposal-only stage. Record operator
acceptance in its own commit before any retention code. An uncovered behavior decision needs
another separately accepted amendment; oversight review is not operator acceptance.
