# Contract 12 — Harness faults are unavailable checks, not failed product tests

Status: **accepted by Juan, 2026-10-14**, with the execution-parity condition below. Acceptance
is recorded in this separate commit before implementation.
Tracks [issue #14](https://github.com/canfieldjuan/local-connect-status/issues/14).

## Operator acceptance and condition

Juan's direct instruction: "I accept Contract 12 as proposed, with one condition: the acceptance
adapter must execute the product script with __main__ semantics in the same interpreter/venv
as today, and a parity test must show the success path is identical to direct execution."

The adapter must execute the script as `__main__`, not import it under an alternate module
name and call `main`. Keep the product interpreter/venv, arguments and environment. A real
fixture subprocess parity test compares direct and adapted success: exit, stdout/stderr,
product side effects, argv, `__main__` identity/file metadata, and interpreter/venv. Observe
native assertions without altering the product source or its success path.

## Accepted amendment — connect-contracts counted unittest step

Operator decision relayed verbatim by oversight on PR #30, 2026-10-14:
"Yes, amend Contract 12 (Recommended)". The accepted option was: "Codex adds the counted
result for the contracts test step to the plan and code in the same PR. A real test failure
stays "check failed" and broken tooling becomes "harness fault". Nothing else changes."
This amendment is committed by itself before its implementation.

The connect-contracts `unittest discover -s tests` step in `accept_ew_ds` is a test phase,
not preparation. An LCS-owned `lcstatus/unittest_proof.py` adapter uses the same uv command,
requirements, interpreter pin, exact tree and discovery arguments, without editing
connect-contracts. It uses the framework's native result callbacks and prints one terminal
stdout line, only after the runner returns normally, prefixed `LCSTATUS_UNITTEST_RESULT `:

```json
{"schema_version": 1, "runner": "accept_ew_ds", "phase": "contracts-test",
 "tests": 2, "failures": 1, "errors": 0, "skipped": 0}
```

`tests` is the native `testsRun` count. Failures and errors count distinct parent tests
observed through the native callbacks, including subtests; skips count whole-test skips
(subtest skips do not mark the parent unexecuted); unexpected successes count as
failed tests, expected failures retain unittest's successful outcome. One test can fail and
also error in teardown, so failure and error counts can overlap. Counts must be strict
nonnegative integers, each at most `tests`, with failures plus skipped at most `tests`.
`executed` is `tests` minus skipped. The existing detail can carry the error diagnostic.

The same `outcomes.classify_execution` owner validates the exact keys, version, runner,
phase and counts. Duplicate, nonterminal, missing or malformed reports are unavailable.
A normally completed exit one with at least one counted failed test is fail. Errors-only,
other nonzero exits, kill/signal, timeout, startup and earlier uv fetch faults are unavailable.
A valid zero-exit report with no failures or errors allows the later build/proof steps;
a valid zero-count/all-skipped result retains skip and also allows those steps, as before.
Every other outcome stops the remaining steps, with its report-derived record and log.

Required fail-first fixtures: counted failure, errors-only, missing report and fetch failure,
plus a mutation bypassing the report. The original settling evidence and operator parity
condition still apply. No Record field, verdict, skip/retry, historical row, evidence
identity or other receipt changes are authorized by this amendment.

## Accepted amendment — new unavailable harness reason identity

Operator decision relayed verbatim by oversight on PR #30, 2026-10-14:
"Yes, narrow amendment (Recommended)". The accepted option was: "New unavailable rows count
as new when the harness reason changes. The same fault repeated still collapses into one row.
Reasons must not contain anything that changes every run, such as times or process IDs, and
a test proves it. History, test failures and retry rules are unchanged."

Before code, this separate amendment adds `lcstatus/evidence.py:Record.identity` to scope,
only for new `automated_test` / `unavailable` rows with a nonempty `detail.harness_reason`.
Their content identity includes that reason. Phase is not added: it already participates in
the owner-produced reason when needed, and a phase-only difference is not a new fault.
Series identity, check fingerprints, skip/decided predicates and all other kinds/verdicts
remain unchanged. Old rows are never rewritten or reinterpreted.

The classifier owns a stable reason descriptor: ISO/compact run timestamps, explicit process
ID tokens and measured elapsed-duration tokens are replaced with stable placeholders; raw
external environment-setup stderr is diagnostic detail, not the identity-bearing reason.
Prerequisite names, exit codes, exception types, signal names and fixed timeout limits retain
their meaning. Execution logs/timing metadata do not enter identity. Fixture runs differing
only in timestamps, process IDs or measured duration must collapse; a truly changed reason
must append, preserving the previous JSONL bytes. Untagged historical unavailable rows and
all failures retain their existing identity behavior. A mutation dropping the reason from
identity must fail this test. No additional verdict, schema field or retry rule is added.

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

## Accepted amendment — PDF proof entitlement input

Juan's decision, relayed verbatim by oversight on PR #33, 2026-10-16:
"Accept the change (Recommended)". The accepted option was: "Dashboard supplies the
automations-enabled test licence from the exact contracts revision; refuses (unavailable)
if it's missing, no fallback; full proof unchanged. Then it's built with tests and merged
on green + review."

The decision covers proposal head `e8f5881bc00d439262b1eb82095b3b5a1a0138ae` and is recorded
in [oversight's acceptance comment](https://github.com/canfieldjuan/local-connect-status/pull/33#issuecomment-6208219112).
This acceptance is committed by itself before implementation for
[release work order #7](https://github.com/canfieldjuan/local-connect-status/issues/7#issuecomment-6207926575).
The accepted execution-parity, classification,
receipt, history and retry requirements above remain binding.

### Evidence and source owners

The recorded PDF attempt `bd512894b39eda50919681d4`, at `2026-10-15T08:33:44Z`, used
Email Watcher `43a850d8b00cddfdad2a767b7b27fc792707b010`, Document Summarizer
`62cc1ca69ea097a0fe87ab12a51c7251ac7c330e`, and connect-contracts
`0f77f8a77e6fb014f0d563645ebdbb83ce6dbc3b`. Its contracts step counted 79 tests with
no failures or errors, and the provider build completed. The proof then raised
`The supplied active Connect entitlement does not include automations` before reaching
its product-check report. Contract 12 correctly classified this as unavailable, not fail.
This historical log establishes the observed input fault; no fresh end-to-end reproduction
or passing product proof is claimed by this proposal.

- Dashboard commit `26ccfebc0739f1fab9065f0c37f15c04cbccb522` introduced the selection of
  `entitlements/v1/fixtures/valid/active.json` in `Runner.accept_ew_ds`. At dashboard
  `5ea0363a7b4516e0702dc7eefe129926ec333353`, that owner is `lcstatus/verify.py:942`,
  and the active-input selection is at line 958. At the recorded contracts revision,
  this public signed fixture grants only `connect.capability_exchange`.
- Email Watcher commit `b538f8febbb016363bf324348381d6d481e78ad9` introduced the additional
  entitlement precondition in `scripts/connect-local-proof.py::main`, together with
  Contract Watch execution and its native `contract_watch_*` product checks. At the recorded
  Email Watcher revision, the precondition is at lines 604–608. Its product contract,
  `docs/CONTRACT_WATCH_AUTOMATION_CONTRACT.md`, was accepted in commit
  `490195aa3170906f7b3024d25731ebb12e7f0e10` and requires both
  `connect.capability_exchange` and `connect.automations` for that path.
- At the recorded contracts revision, the existing signed fixture
  `entitlements/v1/fixtures/valid/active-automations.json` grants both features and is
  paired with the existing fixture keyring. No fixture is fabricated or re-signed here.

The dashboard's input selection predates the product's expanded proof. Contract 12 explicitly
preserves the existing fixtures and arguments. Supplying the additional feature therefore
needs acceptance even though the unavailable classification itself is correct. The proposed
fix belongs to `Runner.accept_ew_ds`; the classifier and the product precondition do not need
a workaround.

### Accepted bounded change

Authorize `Runner.accept_ew_ds` to supply the exact declared connect-contracts tree's
`entitlements/v1/fixtures/valid/active-automations.json` as `--active-entitlement`.
Keep `test-keyring.json`, `valid/expired.json`, the PDF, participant identities, contracts
test step, provider build, stand-in model and software rendering unchanged.

Require the automations fixture as an input before preparation. If absent, return the
existing unavailable input-fault observation naming that path; do not fall back to
`active.json`, an installed licence, another revision, or generated entitlement bytes.
The product remains the owner of signature, validity-window and feature validation.

Execute the unmodified exact product script with the accepted adapter's `__main__`
semantics in the same interpreter/venv, with the existing isolated HOME/XDG and process
ownership. Exercise its full current proof, including its native Contract Watch checks;
do not bypass its feature guard or remove checks. The existing completed zero-exit PDF
success rule and witnessed native `require_proof_checks` failure rule remain unchanged.
The proof's precondition, startup or licence fault without a native product-failure receipt
remains unavailable under the shared classifier.

This does not add a catalogue check, condition or gate, or grant installed automation,
real-model, OCR, human-demo, Windows, promotion or public-release evidence. In particular,
the stand-in-model exact-tree proof cannot satisfy the separate installed automation demos.
Existing catalogue claims and labels retain their current owners and meanings. Old records
are untouched; normal collector-code identity and retry rules apply to new executions.

Implementation scope, only after acceptance: `Runner.accept_ew_ds` in `lcstatus/verify.py`
and regression fixtures in `tests/test_verify_parsing.py`. No product edits, fixture edits,
classifier/adapter changes, new diagnostics, live-system actions or catalogue changes.

### Settling evidence after acceptance

All regression executions use temporary runtime roots, synthetic exact-revision trees and
stub preparation tools. They never install or execute the live apps or use a real licence.

| Requirement | Evidence |
|---|---|
| Exact proof input | Fail-first runner fixture inspects the actual proof argv and fixture bytes: the automations-enabled input comes from the declared contracts revision; legacy `active.json` remains present with different bytes and cannot be selected. All three participant revisions remain bound. |
| Missing required fixture | With legacy `active.json` present but the automations fixture absent, record unavailable naming the missing input before any environment/build/proof step. No fallback or product-failure claim. |
| Success and product-failure twins | A fixture script with the native feature precondition succeeds with the required input through the real acceptance adapter; a witnessed false native product check still yields fail. Preserve the accepted adapter parity test and all existing PDF success requirements. |
| Harness-fault twin | A feature-rejected input without a native product-check report remains unavailable through the shared classifier; do not weaken or reinterpret that guard. |
| Source-owner mutations | Reverting the input selection to `active.json` must fail the input/success regression; adding a fallback to it must fail the missing-input regression. |
| Exact-tree reproduction | In task-owned scratch roots, reproduce the recorded input rejection at the recorded participant revisions, then observe the newly authorized input through the same runner. Record actual outcomes; another fault is not a pass and is classified before further work. No product source edits or live-cache hardlinks. |
| Release gate and live proof | Full repository suite once on the final changed head with the repository .venv; bot reconciled, exact-head oversight PASS, clean local/remote head and zero unresolved threads before merge commit. After merge, read the next deployment and PDF records, report their actual state, and post live evidence. |

The proposal was committed and opened by itself; implementation, test gates and fresh
product-proof execution stopped pending acceptance. The operator's acceptance above is
recorded verbatim in its own commit before code. Implement only this accepted fixture
change and bounded proof scope; do not substitute a narrower proof silently.

## Proposed amendment — select the documented PDF proof build (awaiting acceptance)

This is a second, separate decision. The entitlement amendment above is accepted and its
bounded implementation is committed at `8f29710fcff37473e9d215a5a8dd9eee612d2b70`.
Its final-head suite passed once: 614 tests in 189.89 seconds. The two required entitlement
mutants failed their regressions. This proposal does not claim that the real PDF proof passed.

### Observed next fault and owner

The task-owned scratch baseline reproduced the historical rejection using the exact recorded
three participant revisions above. After only the accepted entitlement selection changed,
the actual runner passed that precondition and again counted 79 successful contracts tests,
but the PDF invocation stopped with `MODEL_CONFIG_INVALID`,
`The configured local model runtime is unavailable.` It did not reach a witnessed native
product-failure assertion, so the shared classifier correctly returned unavailable.

At dashboard `8f29710`, `Runner.accept_ew_ds` in `lcstatus/verify.py:994` selects
`npm run desktop:build:no-bundle`; that selection was introduced by dashboard commit
`26ccfebc0739f1fab9065f0c37f15c04cbccb522`. It builds the ordinary provider without its
opt-in fixture runtime.

Document Summarizer commit `15781d340a71c7d6420bab6e2543b977415d25af` introduced the
separate proof build and runtime boundary. At the recorded provider revision,
`src-tauri/src/connect/provider.rs::select_connect_runtime_source` (lines 111–130) selects
persisted product settings when that feature is absent, regardless of the proof environment.
`provider_runtime_factory` (lines 132–149) supplies the compiled-feature observation to that
owner. This is intentional product behavior. `docs/PR-CONNECT-PROOF-RUNTIME.md` describes
the same empty-settings fault and explicitly requires `npm run desktop:build:connect-proof`
for this deterministic cross-process proof; `package.json:12` defines it as
`tauri build --no-bundle --features connect-proof-runtime`.

Email Watcher's exact proof already supplies the versioned fixture mode, digest and
loopback fixture model configuration. No real model, operator settings or GPU is required
or proposed. The remaining mismatch is the dashboard's build-command selection, owned by
`Runner.accept_ew_ds`, rather than a product defect or a verdict-classifier defect.

### Proposed additional authorization

Change only this runner's provider build command from `desktop:build:no-bundle` to the
exact provider tree's existing `desktop:build:connect-proof`. Keep the accepted entitlement
input, contracts test step, npm preparation, isolated proof directories, exact revisions,
adapter, native full proof and classification rules unchanged. Do not edit any product,
introduce another runtime, copy model settings, use a real model, install the proof binary,
publish an artifact, bypass runtime admission, add fallback behavior, or weaken checks.

The feature-enabled binary remains a task-local acceptance artifact. This is a stand-in-model
exact-tree check, not evidence about the normal production build, model qualification,
installed apps, real-model operation or human demos. No catalogue claim, condition, release
gate or label changes. The existing unavailable result remains honest until fresh admitted
evidence supersedes it.

Implementation scope, if accepted: the build command in `Runner.accept_ew_ds` and the
existing runner regression fixtures in `tests/test_verify_parsing.py`. All other boundaries
of the accepted entitlement amendment remain binding.

### Required evidence after acceptance

- A fail-first runner regression checks the actual build argv selects the dedicated proof
  build and still binds the provider output, full native proof, exact participants and the
  accepted automations entitlement. Preserve all existing assertions; update the expected
  build command only to the newly accepted command.
- Stub-tool failure/timeout controls remain unavailable through the shared classifier;
  successful preparation alone cannot produce a proof pass. Keep the native success,
  product-failure and harness-fault twins, including the missing-entitlement refusal.
- A source-owner mutation selecting the ordinary no-bundle build must fail the build-argv
  regression. Retain the entitlement-selection and fallback mutations.
- After that code change, run the actual full proof in the existing task-owned scratch
  roots at the recorded participant revisions with the dedicated proof build. No product
  source changes or live-cache hardlinks. Report its actual terminal outcome; another fault
  must be classified before advancing.
- Run the full repository suite once on the final changed head with the repository .venv.
  Reconcile the bot, obtain exact-head oversight PASS and zero unresolved threads, then
  merge as a merge commit. Read and post actual live deployment and next PDF-record
  evidence; do not operate the live service or fabricate closure.

This proposal is committed separately before any build-command implementation. Ask Juan
through oversight to accept this additional bounded input change, then stop. Acceptance
must be recorded verbatim in its own commit before code. If declined or deferred, retain
the current unavailable result and request disposition of the partial entitlement fix;
do not silently select another build or narrow the proof.
