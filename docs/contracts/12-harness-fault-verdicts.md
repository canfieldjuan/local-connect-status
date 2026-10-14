# Contract 12 — Harness faults are unavailable checks, not failed product tests

Status: **proposed; awaiting operator acceptance**. No implementation is authorized by this
document until Juan's acceptance is relayed verbatim and recorded in a separate commit.
Tracks [issue #14](https://github.com/canfieldjuan/local-connect-status/issues/14).

## Problem and inspected evidence

At dashboard main `08009142732676253d6dcb1ebc8d595264ef0ea2`:

- `Runner.pytest` labels every nonzero exit `fail` before considering its JUnit counts.
- `Runner.cargo_lib` labels an npm install/build or cargo fetch/build exit `fail`, even without
  a cargo test-result report.
- `Runner.accept_ew_ip` treats a nonzero exit, or zero without the complete success proof, as
  `fail`. `Runner.accept_ew_ds` labels setup failures `fail` and invents `failed=1` from an exit.
- `rules.condition_status` turns those current `fail` records into `check_failed`. The page
  therefore calls a broken harness a failed product check.
- Contract 07 already makes uncounted failures retryable in `verify.decided`. Issue #14 was
  deferred from that work; this contract fixes classification and wording without changing
  that skip decision.

Product scripts were read, never executed or modified, through owned bare mirrors:

- Invoice Processor `20b1721cb2e15a9d30e396afa56196b904581b06`,
  `scripts/accept_against_email_watcher.py`: `require(checks, name, condition)` raises at a
  false assertion; the complete nine-check JSON proof is emitted only on success.
- Email Watcher `43a850d8b00cddfdad2a767b7b27fc792707b010`,
  `scripts/connect-local-proof.py`: `require_proof_checks(checks)` raises when its product
  check map contains false values. Several earlier exceptions emit no completed proof.

The failure-proof producer boundary must therefore be explicit. An exception message,
stdout prose or nonzero exit is not proof of a product failure.

## Scope

- New `lcstatus/outcomes.py`: the one execution-outcome classifier, including validation of
  framework reports and machine-readable acceptance receipts.
- `lcstatus/verify.py`: pytest, cargo_lib, accept_ew_ip and accept_ew_ds use that classifier on
  every preparation, execution and abnormal-return path. Runner-specific code supplies the
  phase and observations, not verdict decisions or copies of the classification rule.
- New `lcstatus/acceptance_proof.py`: an LCS-owned adapter for the two exact-tree acceptance
  scripts, producing the receipt defined below from native product assertions.
- `lcstatus/rules.py`: the sole owner of the harness-fault condition label.
- `lcstatus/render.py`: carry that derived label through existing JSON, Markdown and HTML
  rendering and in-place refresh. README and fixture tests.

No product repository or extracted product source is edited. No installed build, promotion,
release, CI-job, source-inspection or deployment verdict rule changes. Catalogue claims,
licence staging, isolation, process ownership, locks, fingerprints and immutable log paths
retain their existing behavior. Issues #23, #27 and #29 are out of scope.

## R1 — One classification rule for every executing local runner

`outcomes.classify_execution` owns the outcome. Its inputs identify the runner, exact phase,
exit/termination observation and that execution's framework report or acceptance receipt.
It returns the existing verdict, honest counts and a reason. Parsers belong to that same owner;
no runner decides an exit is `fail` by itself.

Precedence:

1. Missing prerequisites, launch errors, timeout, a signal/kill or interrupted/incomplete
   execution are `unavailable`, with the observed harness reason. Do not infer OOM merely
   from SIGKILL; record the signal/exit and say OOM only when it was actually observed.
2. Dependency fetch/install, build, licence staging, tool/Xvfb startup and other preparation
   phases cannot furnish failed-product-test evidence. A nonzero preparation exit is
   `unavailable`, even if it printed a test-looking summary or JSON.
3. A completed test/proof phase is `fail` only when its own validated report establishes a
   failed product test under R2 or R3. Nonzero without that evidence is `unavailable`.
4. Existing positive success requirements remain: successful pytest/cargo need counted tests;
   invoice acceptance needs its complete nine-check proof. PDF acceptance's existing
   completed zero-exit success behavior is retained. Normal zero-exit all-skipped/zero-run
   framework reports retain `skip`; missing/malformed reports are abnormal and unavailable.
   A nonzero "no tests collected" exit is always unavailable.

Every new unavailable harness record stores `detail.harness_reason` as a nonempty string and
a summary `harness fault: <reason>`. Phase, actual exit/signal and safe diagnostics identify
the fault; never include a licence payload, credential or secret in the reason. Logs keep
the existing immutable paths. Existing `Record` fields and verdict states are reused.

## R2 — Counted framework failures

- **pytest:** the current execution's well-formed JUnit report must count at least one actual
  failed test (`failures`/testcase failure evidence), and the execution must have pytest's
  ordinary failed-tests exit, 1. Collection/usage/no-tests exits 2, 4 and 5, negative signal
  returns, absent/malformed/invalid counts and errors without a counted failed test are
  unavailable. JUnit collection/setup errors do not become failed-test proof. Keep error
  counts as diagnostics in existing `detail`; do not invent a failed count from an exit.
- **cargo_lib:** only the actual `cargo test --lib` phase's well-formed `test result:` summary
  can supply counts. At least one counted failed test on a normally completed test-failure
  exit proves `fail`. npm/crates fetch, compilation, OOM/kill, usage/startup and absent summary
  cannot. Earlier step output cannot supply the test phase's report. Signal/interrupt takes
  precedence over a partial report.
- Validate counts as nonnegative integers and reject inconsistent reports. Preserve executed,
  failed and skipped counts only when the report actually supplies them; otherwise use null,
  not `failed=int(nonzero)`.

## R3 — Acceptance scripts print machine-readable product-failure proof

The LCS-owned adapter loads only the exact extracted script and calls its entry point with
the same arguments, isolated HOME/XDG, licence, fixtures and process ownership as today.
It leaves the extracted files untouched. It observes the native assertion functions below
and preserves their behavior and exceptions. It neither catches arbitrary exceptions and
calls them product failures nor manufactures proof from exit codes or traceback text.

On a witnessed native product assertion failure, after the script's cleanup completes and
the same witnessed assertion exception propagates, the adapter prints exactly one terminal
stdout line prefixed `LCSTATUS_ACCEPTANCE_RESULT ` followed by JSON of this shape:

```json
{
  "schema_version": 1,
  "type": "product_failure",
  "runner": "accept_ew_ip",
  "checks": {"ledger_one_entry": false},
  "failed_checks": ["ledger_one_entry"]
}
```

`runner` is exactly `accept_ew_ip` or `accept_ew_ds` for the executing adapter. `checks` is a
nonempty native-observation map with nonempty check-name strings and strictly boolean
values. `failed_checks` is the nonempty, sorted, duplicate-free set of precisely its false
entries. The classifier rejects a wrong version/type/runner, nonboolean value, malformed or
duplicate/conflicting receipt, and a receipt not produced in the acceptance phase. The
adapter exits normally with status 1 for a witnessed product failure; kill, timeout, launch
or cleanup failure cannot turn a partial witness into a completed failure receipt.

- **Invoice adapter:** witness only `require` with an actual false boolean for one of
  `provider_discovered`, `invoice_job_completed`, `declared_outputs_present`,
  `ledger_one_entry`, `replay_no_duplicate`, `second_provider_refused`, or
  `registration_removed`. `consumer_import_pinned` and `entitlement_active` are harness
  prerequisites, not product-failure proof. A missing/unstaged/rejected licence is unavailable.
  The existing complete success JSON and its exact nine booleans remain the success gate.
- **PDF adapter:** witness the native `require_proof_checks` call with a strictly boolean
  map containing at least one false product check. The map is passed to the native function;
  no check is skipped or made to pass. Earlier launch, Xvfb, licence, setup, arbitrary
  exception or incomplete proof paths without a witnessed product-check report are unavailable.
- An unsupported script revision without the expected native assertion hook is unavailable,
  with an explicit reason. Adapting a new hook or adding a product failure site needs its own
  accepted decision; no edits to product source or guessed interpretation are allowed.

These are the receipt changes explicitly proposed by this contract, confined to the local
adapter/collector boundary. They are not changes to product wire protocols. No new verdict
state or `Record` schema field is introduced. Future receipt changes beyond this definition
hit the drift gate.

## R4 — History and retry semantics

- **New records only.** Never rewrite, delete, backfill or reinterpret old `records.jsonl`
  rows. A historical uncounted `fail` remains historical `fail`; a later freshly classified
  observation can supersede it under the existing ordering/admission rules.
- `verify.decided`, its predicates and the runner table's failure-skip column remain unchanged.
  New unavailable rows retry as before. Pytest/cargo counted failures retain their existing
  skip behavior. Acceptance product failures remain retryable because those runners still
  have no failure-skip predicate. Adding a receipt does not grant a new skip decision.
- The existing collector-code fingerprint naturally changes when the owner/adapter changes;
  the usual first execution after a collector change is retained. No special rerun or key
  exception is added.

## R5 — Page wording comes from records and rules

For an admitted new automated-test `unavailable` record with `detail.harness_reason`, keep
the existing `not_checked` condition state and unavailable/not-checked styling, and derive
its label in `lcstatus/rules.py`:

`check unavailable: harness fault (<reason>)`

Use that same derived label in `site/status.json`, `report.md` and `dashboard.html`, including
in-place refresh. Escape the reason at output boundaries. The evidence keeps its actual
unavailable verdict and log link. Neither condition nor task freshness calls this new
unavailable result "check failed"; existing freshness precedence for other actual failures
is unchanged. Source-failure/banner handling remains as today. Old rows and unavailable
sources without the new marker retain their existing wording.

## Settling evidence required after acceptance

All fixtures use temporary runtime roots, synthetic exact-revision product trees, stub tools
and small scripts; never run actual product acceptance, stage a real licence, use a GPU or
inspect/mutate live stores or services.

| Requirement | Fail-first fixture and positive twin |
|---|---|
| pytest kill | Exit -9 without JUnit is unavailable with observed signal reason; do not assert an unobserved OOM cause |
| pytest usage/no collection | Exits 4 and 5 are unavailable, including empty reports; exit 2 with collection errors is unavailable |
| pytest failure proof | Exit 1 with a valid counted failed test stays fail; summary prose alone, malformed/negative counts or errors alone cannot supply that proof |
| fetch faults | uv/npm/crates fetch failures are unavailable with phase/reason across the affected runners; test-looking output in a setup phase is ignored as product evidence |
| build faults | npm/rustc/Tauri build nonzero and kill are unavailable; cargo test's counted product failure stays fail |
| licence | Missing, unstaged or rejected licence is unavailable; a typed product assertion receipt after prerequisites stays fail |
| Xvfb/startup/timeout | Xvfb failure, process start error, timeout and incomplete proof are unavailable with reason; no failed count is synthesized |
| both acceptance adapters | Realistic fixture native hooks emit valid failure receipts; malformed/wrong-runner/conflicting/boolean-shaped receipts fail closed; a cleanup error or kill after a witness gives unavailable; original positive gates still pass |
| shared owner | Exercise every executing runner's preparation and terminal paths through the classifier with behavior/call evidence; no source-substring-only owner test |
| history | Mixed old fail/new unavailable records: bytes of the old JSONL prefix unchanged, old verdict untouched, new row selected by normal rules; no render-only reinterpretation |
| retry | Keep the Contract 07 skip table unchanged; an unavailable harness result reruns, acceptance fail still reruns, counted pytest/cargo failure remains decided |
| rendering | JSON/Markdown/HTML and refresh show the same escaped harness reason in the existing styling, without check-failed wording for the new row; true failure and unrelated labels remain unchanged |
| mutations | Dropping the counted-failure requirement makes a harness fault fail and must be caught; bypassing the shared classifier in one runner must also be caught by its runner fixture |
| release gate | Full repository suite once on the final changed head using the repository .venv; reconcile bot findings; exact-final-head oversight PASS; clean local=remote tree and zero unresolved threads before a merge commit |

No implementation suite or mutation gate is run during this contract-only stage.

## Release and live observation boundary

Since Contract 11, merging to main releases this dashboard code to the next collection tick
(every twenty minutes) through its own test gate and rollback. Treat the merge as release,
not an inert source-only handoff. Do not squash.

After implementation merge, obtain a passive next-tick deployment observation through
oversight/operator: attempted revision, deployment outcome, and the rendered Dashboard code
row with `current` equal to the actual merge SHA and green. Report the observation and stop.
A failed/rolled-back or unavailable observation is reported honestly; it does not authorize
live repair. Do not read/write the live data root, operate units, update the old checkout,
manually collect, deploy or roll back. Any required live-system action hits the drift gate.

## Stop-and-ask gates and acceptance

Stop and ask oversight for any new verdict state, schema/wire change beyond this accepted
definition, history rewrite, changed skip/retry rule, live-system action or work on another
issue. Oversight is review only; its notes are not operator authority. Only Juan's decision,
relayed verbatim, counts as acceptance.

Every message uses registered sender `lcs-harness-14`, repository
`canfieldjuan/local-connect-status`, and the actual contract/implementation PR number.
Commit this proposal by itself, open the contract-only PR, request Juan's acceptance via
oversight, and stop. Record an accepted operator decision in its own commit before code.
