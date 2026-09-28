# Contract 09 — The first release is the Local Connect bundle, with its automations and its licence

Status: **proposed 2026-09-27, revision 2**, awaiting the operator's acceptance. This is release step 3 of
invoice-processor#94 (private): the operator's decision of 2026-09-27 and its cross-session order of
operations. Revision 2 adds `document-ocr` after the operator's decision that reading a scanned PDF needs no
licence and is part of the shared runtime (#94).

Commercial terms are private until launch and are not stated anywhere in this repository. Where a
condition depends on them, it cites "the licensing model in invoice-processor#94 (private)".

Scope:
- `catalogue.json`: the release block; new installed-demo conditions on the three automation tasks and the
  four release rows; two repositories; and the new checks those need;
- `lcstatus/catalogue.py`: one automation-set validation;
- `docs/RELEASE_CONTRACT.md`, README, tests.

Out of scope: rules, fingerprints, render code, and every product repository.

## Problem (evidence, not description)

- **The operator changed what the first release is** (invoice-processor#94, private):
  - **The product is the Local Connect bundle.** One download installs the three apps, the shared model
    runtime and host (invoice-processor MODEL-SETUP revision 8), and one copy of the model.
  - **Installing a single app still works.** It sets up the shared runtime if it is absent.
  - **Automations:** the three cross-app automations ship in the first release.
  - **Licensing** uses entitlement v1 unchanged (connect-contracts ADR-0003, ADR-0004, ADR-0006), including
    the hand-signed offline licence.
  - **Without a valid licence**, one-click handoffs (`connect.capability_exchange`) and the automations
    (`connect.automations`) stop. Standalone use of every app, and the user's own data, always keep working.
  - **The privacy promise:** the user's emails, invoices and documents are processed on their computer and
    never uploaded. The apps go online only for the destinations the promise names (#94), never with
    document contents.
  - **Reading a scanned PDF needs no licence, and is part of the shared runtime** (#94, answering the review of this
    contract's revision 1).
    - The OCR provider installs with the bundle, or with any single app that needs it.
    - A local application's OCR request needs no licence.
    - Handoffs and automations stay licensed.
- **The release definition says the opposite in five places.**
  - `docs/RELEASE_CONTRACT.md:3`: "Automate rules and scheduled workflows are planned for later releases
    and do not block the first public release."
  - `docs/RELEASE_CONTRACT.md:30` puts "Automate work" after the first release.
  - `catalogue.json:10-14` sets `automate_scope` to `required_for_first_release: false`.
  - The bundle row's `human_involvement` (`catalogue.json:1001`) says Automate "remain[s] a later release".
  - `README.md:196-197` lists requiring Automate among the things the dashboard must not do.
  - `tests/test_release_contract.py:86` pins `required_for_first_release is False`.
- **Nothing makes the bundle wait for the automations.** They are three `automate` tasks:

  | # | task | proof today |
  |---|---|---|
  | 1 | `ew.meeting_suggestions_and_confirmed_write` | an installed demonstration on Linux only: `manual.ew_live_calendar` (`catalogue.json:286`, platform `linux`) |
  | 2 | `automate.unattended_pdf_summary` | only `source_inspection` conditions |
  | 3 | `automate.invoice_intake_and_digest` | only `source_inspection` conditions |

  `source_inspection` conditions can never raise maturity above `planned` (`lcstatus/rules.py`, module
  docstring). The bundle row (`catalogue.json:996`) names none of these checks, so the bundle can read
  **ready for release** without one automation ever demonstrated.
- **Nothing requires the rest of the product either.**
  - The bundle row's 17 conditions cover:
    - handoffs, busy retry and local entitlement verification;
    - installed handoff demonstrations;
    - the three app publications;
    - four issue gates.
  - None of them asks for the bundle download, the shared runtime, the online licence, behaviour without a
    licence, the offline licence, or the privacy promise.
  - The app release rows likewise do not ask that an app installed alone sets up the shared runtime.
- **Scanned PDFs are read only by `document-ocr`, which the release definition leaves out.**
  - Document Summarizer consumes it over Connect (`src-tauri/src/connect/ocr_consumer.rs:36-38`:
    `document-ocr`, `document.ocr` 1.0). Invoice Processor does too, through its `ocr-providers` and
    `ocr-recoveries` (`desktop/src-tauri/src/lib.rs:74,86`).
  - Its endpoint refuses a caller without the Connect entitlement (`src/document_ocr/server.py:76-82`,
    `NOT_ENTITLED`). So without a licence, neither app can read a scan today, even standalone.
  - Its contract keeps Windows out of scope (`docs/contracts/DOCUMENT-OCR-V1.md:19`).
  - It is not a catalogue repository, and it has no "First Public Release" milestone: `gh api
    repos/canfieldjuan/document-ocr/milestones?state=all` listed none on 2026-09-27.
  - As revision 1 was written, a demonstration of automation 2 or 3 could pass using only PDFs that
    already carry text, without ever reading a scan.
- **The automation code's repository has no issue gate.**
  - Automations 2 and 3 are built in Email Watcher and `connect-automate` (#94, codex step 4).
  - `connect-automate` is not a catalogue repository: contract 08 D3 deferred it because nothing then
    needed it.
  - A release-blocking automation bug filed where that code lives therefore blocks nothing.
  - Its "First Public Release" milestone was created on 2026-09-27; `gh api
    repos/canfieldjuan/connect-automate/milestones?state=all` now lists it, open.

## Observable behaviour

B1. **Release block.**
- `release.target` becomes "The Local Connect bundle download for Linux and Windows. Each app may also be
  released on its own."
- `release.automate_scope` becomes:
  - `decision`: "in the first release: the three cross-app automations";
  - `required_for_first_release`: `true`;
  - `tasks`: the three task ids above, in order;
  - `note`: "The bundle is not ready for release until each automation is demonstrated installed on Linux
    and Windows, together with every automated check its task names."
- The dashboard's first-release scope banner and report section print `decision` and `note` exactly as
  today. Render code is unchanged.

B2. **Automation tasks: an installed demonstration on each platform.**

| task | new condition | check (new) | repo · participants | platform |
|---|---|---|---|---|
| 1 | `ew.live_calendar_demo_windows` | `manual.ew_live_calendar_windows` | eom-email-watcher | windows |
| 2 | `auto.pdf_summary_installed_linux` | `manual.auto_pdf_summary_linux` | document-summarizer · EW, DS, OCR, contracts | linux |
| 2 | `auto.pdf_summary_installed_windows` | `manual.auto_pdf_summary_windows` | document-summarizer · EW, DS, OCR, contracts | windows |
| 3 | `auto.invoice_digest_installed_linux` | `manual.auto_invoice_digest_linux` | invoice-processor · EW, IP, OCR, contracts | linux |
| 3 | `auto.invoice_digest_installed_windows` | `manual.auto_invoice_digest_windows` | invoice-processor · EW, IP, OCR, contracts | windows |

What each demonstration asserts, stated in each condition's `proves` and each check's `note`:
- **Task 1:** the Linux demonstration's steps, on the installed Windows app.
- **Task 2:** with the installed apps and a licence granting `connect.automations`:
  - the person defines a rule once ("this sender, any PDF");
  - a matching mail's PDF is summarized with no click, and the notification carries the summary;
  - at least one matching mail carries a scanned PDF with no text layer, and it is summarized through
    OCR;
  - a non-matching mail is not handed over.
- **Task 3:** under the same conditions:
  - the person defines a vendor-and-bill rule;
  - each matching bill is filed into the ledger with no click;
  - at least one matching bill is a scanned PDF with no text layer, and it is filed through OCR;
  - a busy provider leaves the bill queued, and it then completes;
  - the scheduled digest lists what arrived and what is due;
  - nothing is paid, and review before payment stays with the person.

`manual.ew_live_calendar`'s `note` gains "on the installed app" (D7). The existing conditions of all three
tasks are unchanged.

B3. **Bundle row.** `release.local_connect_bundle` keeps its 17 conditions and gains the following.

*The automations*, by sharing their tasks' checks, as the row already does with eleven checks it shares
with connect and app rows:

| condition | check | kind | platform |
|---|---|---|---|
| `rel.bundle_auto1_extraction` | `ew.pytest.scheduling` | automated_test | linux |
| `rel.bundle_auto1_gated` | `ew.pytest.automation` | automated_test | linux |
| `rel.bundle_auto1_linux` | `manual.ew_live_calendar` | installed_demo | linux |
| `rel.bundle_auto1_windows` | `manual.ew_live_calendar_windows` | installed_demo | windows |
| `rel.bundle_auto2_linux` / `_windows` | `manual.auto_pdf_summary_linux` / `_windows` | installed_demo | each |
| `rel.bundle_auto3_linux` / `_windows` | `manual.auto_invoice_digest_linux` / `_windows` | installed_demo | each |

*The product*: new `manual_observation` checks anchored on `connect-contracts`.
- Each declares the participants `eom-email-watcher`, `document-summarizer`, `invoice-processor`,
  `document-ocr` and `connect-contracts`.
- There is one check per platform (`…_linux`, `…_windows`), each backing a condition of the same stem.

What a person observes on a clean machine, stated in `proves` and `note`:

| condition stem | observed |
|---|---|
| `rel.bundle_download` | One bundle download installs the three apps and the shared runtime (the model host and the OCR provider), with exactly one model copy and no configuration. Each app's first launch uses that one server: one model file on disk, one server process. Document Summarizer and Invoice Processor each read a scanned PDF with no text layer. |
| `rel.bundle_licence_online` | The apps obtain and keep a valid licence online as the licensing model in invoice-processor#94 (private) specifies, from first launch onward, without user configuration. All three apps verify it locally under entitlement v1 (`connect.capability_exchange`, `connect.automations`), and the handoffs and automations are active in all three. |
| `rel.bundle_without_licence` | With no valid licence (expired or absent): each app's standalone flow and all user data keep working, and Document Summarizer and Invoice Processor still read a scanned PDF with no text layer. One-click handoffs and the automations stop, each showing a plain message. Importing a hand-signed offline licence restores them without going online. |
| `rel.bundle_privacy` | While the automation demonstrations, which include a scan, and the online licence behaviour run, every outbound connection of the installed apps, model host and OCR provider is recorded. Every destination is one the privacy promise in invoice-processor#94 (private) allows, and no request carries document content. The artifact is the capture and its destination summary. |

*Two new issue gates*, each backed by a new `github_issues` check with milestone "First Public Release":
- `rel.bundle_automate_issue_gate`: check `release.issues.automate`, repo `connect-automate`;
- `rel.bundle_ocr_issue_gate`: check `release.issues.ocr`, repo `document-ocr`.

Changes to the row's other fields:
- `human_involvement` becomes "Run the installed bundle, automation, licence and privacy demonstrations on
  both platforms."
- `promise` gains the automation, scanned-PDF, licence and privacy clauses, with no commercial terms.
- `depends_on` gains two entries (B5):
  - `connect-automate: src/connect_automate/**, pyproject.toml, uv.lock`;
  - `document-ocr: src/document_ocr/**, pyproject.toml, uv.lock`.

B4. **App release rows: an app installed alone sets up the shared runtime.**
- Each app release row gains the condition `rel.<ew|ds|ip>_shared_runtime_linux` / `_windows`.
- Each is backed by a new check `manual.<ew|ds|ip>_shared_runtime_linux` / `_windows`, whose repo is the
  app itself.
  - Email Watcher's check declares no participants: it reads no scans.
  - Document Summarizer's and Invoice Processor's checks declare the app and `document-ocr` as
    participants.
- The claim:
  - On a machine without the shared runtime, installing only this app sets up the runtime and model at
    first launch, with no configuration, and its primary flow completes on it.
  - For Document Summarizer and Invoice Processor, that setup includes the OCR provider, and the demo
    includes reading a scanned PDF with no text layer, without a licence.
  - On a machine that already has the runtime, installing this app reuses it, including the OCR provider
    where present, with no second model copy.
- Existing conditions and their evidence are untouched.

B5. **Two repositories join the catalogue: `connect-automate` and `document-ocr`.**

`connect-automate`:
- It is added as `repos["connect-automate"] = {"github": "canfieldjuan/connect-automate", "ci_workflows": []}`,
  shaped like `connect-contracts`: no local check runs in it.
- `automate.unattended_pdf_summary` and `automate.invoice_intake_and_digest` add
  `connect-automate: src/connect_automate/automate/**` to `depends_on`. The change feed then attributes its
  commits to the automations instead of listing every file as unmapped. This is informational only:
  `depends_on` feeds only `change.assess` (contract 08).
- Evidence binding does not change. Released Email Watcher runs the connect-automate commit pinned in its
  `pyproject.toml`/`uv.lock`, and those files stay mapped (contract 08 D3). The new repository adds only
  its head, its change stream and its issue gate.

`document-ocr`:
- It is added as `repos["document-ocr"] = {"github": "canfieldjuan/document-ocr", "ci_workflows": []}`. No
  local check runs in it.
- It is a participant wherever a demonstration reads a scan (B2, B3, B4), so a change to it makes those
  demonstrations "changed since demonstrated".
- It is private, as `invoice-processor` already is. Its head, commit subjects and First Public Release
  issue titles appear on the page the same way (D14).

B6. **The automation set is validated.** `catalogue.load` refuses the catalogue, naming the fault, when
`release.automate_scope.required_for_first_release` is `true` and any of the following holds:
- a. `tasks` is missing, is not a non-empty list of distinct strings, or names an id that is not a task in
  layer `automate`;
- b. there is not exactly one release-layer task with `app` `"bundle"`;
- c. an automation task has a condition, other than `source_inspection`, whose check no condition of the
  bundle row names ("bundle release row does not carry <check> from <task>");
- d. an automation task has no `installed_demo` condition on one of `release.required_platforms` (the
  condition's own `platform`, else its check's).

When `required_for_first_release` is not `true`, `tasks` is optional and none of these checks runs. The
test fixtures that pass `None` load as before.

B7. **`docs/RELEASE_CONTRACT.md`** is rewritten to state B1–B5 as the release definition:
- the bundle download is the product, and each app may release alone;
- each automation is demonstrated installed on both platforms, with every automated check its task names;
- the licence behaviour:
  - online, per the licensing model in #94 (private);
  - without a licence, what stops and what always keeps working;
  - the offline licence;
- reading a scanned PDF is part of each app's standalone use, with no licence;
- the privacy promise's first sentence, and its destination list by reference to #94;
- the bundle is released only when its download is published (the condition arrives with the installer
  repository, D4).

It states no commercial terms. Its issue-gate section names six repositories, and "Automate work" leaves
the list of deferrable work.

B8. **README.**
- The "Require Automate for the first public release" bullet leaves the list of things the dashboard must
  not do.
- The "What the tests prove" table gains the B6 and settling-evidence rows.

## Invariants

I1. **Evidence is never rewritten.** The store stays append-only, and no existing check's or condition's
fingerprint changes:
- every edited existing check changes only its `note`, a prose key (`CHECK_PROSE_KEYS`,
  `lcstatus/evidence.py:66`);
- no existing condition is edited.

This is verified before merge by fingerprinting every check and condition id on `origin/main` under both
catalogues.

I2. **No label rises at merge.** The expected page changes are exactly:
- the new conditions read `no_evidence`;
- the automation-scope banner text changes;
- the bundle row's issue gate now includes `connect-automate`'s and `document-ocr`'s milestones. Until
  `document-ocr` has one, that gate reads **unavailable** (contract 05), so the bundle's readiness fails
  closed.

This is verified live, read-only, before merge. The branch's derivation over the live store and state is
compared task by task with the served page. Every other difference is a defect.

I3. **One place names the automation set:** `release.automate_scope.tasks`. What the bundle must carry is
derived from those tasks' conditions (B6). A proof added to an automation task but not carried by the
bundle fails the catalogue load; it never silently fails to block the release.

I4. **A bundle-level demonstration is bound to all five product repositories' revisions.** A change in any
participant makes it "changed since demonstrated" (the existing participant rule,
`rules._matches_current`).

I5. **No commercial terms in this repository.** No catalogue text, contract, README or test states commercial terms:
prices, licence periods or payment terms. A test scans the tracked text files for these (settling
test 5).

## Failure cases

| Situation | Result |
|---|---|
| every one of the bundle row's 17 original conditions is satisfied and the new ones have no evidence | not ready for release; the missing conditions are exactly those in B3 |
| an automation task gains an automated condition the bundle row does not carry | `catalogue.load` fails, naming the check and the task; the collector does not start |
| an automation task loses its Windows (or Linux) demonstration | `catalogue.load` fails (B6 d) |
| `required_for_first_release` true, with `tasks` empty, unknown, duplicated, or naming a non-automate task | `catalogue.load` fails (B6 a) |
| no bundle row, or two | `catalogue.load` fails (B6 b) |
| `connect-automate`'s milestone is renamed or deleted | its gate is **unavailable**, and the bundle's readiness fails closed (contract 05) |
| the `connect-automate` fetch fails | a source failure, as for every repository; its gate and head are not current |
| an automation demonstration is recorded with a hand-signed licence before the licence service exists | admitted for the automation condition, which asks for a licence granting `connect.automations`, not how it was issued; `rel.bundle_licence_online_*` stays `no_evidence` |
| a bundle demonstration is recorded, then any of the five repositories moves | "changed since demonstrated" (I4) |
| an automation 2 or 3 demonstration uses only PDFs that already carry text | it does not meet the condition's claim, which names a scanned PDF read through OCR, so it must not be recorded as a pass |
| `document-ocr` has no Windows runtime yet | every Windows demonstration that reads a scan stays `no_evidence` until codex's Windows port (#94, codex step 6). The claim is not weakened for Windows (D13) |
| `document-ocr` still refuses an unlicensed local request | `rel.bundle_without_licence_*` and the Document Summarizer and Invoice Processor shared-runtime demos cannot pass until codex's OCR exemption lands (#94, codex step 5) |
| `document-ocr` has no "First Public Release" milestone | its gate is **unavailable**, and the bundle's readiness fails closed (contract 05) |
| the three apps are published before the installer repository exists | the bundle cannot be ready, because the `rel.bundle_download_*` demos are required. "Released" needs ready, so it cannot be reached until those pass; D4 adds the download's publication condition with that repository |
| an old record for a shared check (e.g. `ew.pytest.automation`) predates the new bundle condition | it does not name the new condition, so it does not prove it. The first tick after merge runs `ew.pytest.scheduling` and `ew.pytest.automation` once, because a condition added to a check changes its run key (contract 07 B1) |

## Concurrency model

Unchanged. The first tick after merge does the following, under `data/.lock`:
- clones the `connect-automate` and `document-ocr` mirrors (`Mirrors.ensure`);
- reads their heads and issue gates;
- runs the two shared pytest checks once (contract 07 B1).

`record_observation.py` writes the new manual checks like any other, holding the lock (contract 08 B5).

## Settling test evidence

Unit (`uv run pytest`):
1. `test_first_release_is_the_bundle_with_its_automations` replaces
   `test_accepted_contract_splits_app_and_bundle_release_and_defers_automate`. It asserts:
   - `required_for_first_release is True`;
   - the automation set is exactly the three task ids;
   - the bundle row carries every automation check;
   - the `download`, `licence_online`, `without_licence` and `privacy` demos exist on both platforms;
   - the product demos' participants are the five repositories;
   - `document-ocr` is a participant of the automation 2 and 3 demos, and of Document Summarizer's and
     Invoice Processor's shared-runtime demos;
   - those conditions' claims, and `rel.bundle_without_licence`'s, name a scanned PDF;
   - the issue checks are the six gates;
   - each app row has shared-runtime demos on both platforms;
   - the entitlement checks are unchanged.
2. `test_catalogue_rejects_an_automation_set_the_bundle_does_not_carry` breaks the committed catalogue on
   purpose, one fault at a time: one case for each of B6 clauses a to d. Each is refused with its message.
   On the other side, the committed catalogue loads, and so does one with `required_for_first_release: false`
   and no `tasks`.
3. `test_old_bundle_evidence_does_not_make_the_new_bundle_ready`:
   - Records at current heads satisfy every original bundle condition, with fingerprints computed as the
     writers compute them. The row is not ready for release, and its unsatisfied conditions are exactly the
     B3 set.
   - Adding passing records for that set makes the row ready, so the new conditions are the only difference.
4. `test_an_automation_demo_record_satisfies_both_rows`: one `record_observation`-shaped record for
   `manual.auto_pdf_summary_linux` names both `auto.pdf_summary_installed_linux` and
   `rel.bundle_auto2_linux`, and both read `satisfied`.
5. `test_no_commercial_terms_in_the_repository` (I5):
   - It scans the tracked text files for price and currency amounts, and for licence periods expressed in
     days or months.
   - It is proven on both sides: a planted line of each kind is caught, and the committed tree is clean.

Live, read-only, before merge (reported on the PR):
- I1: fingerprint equality for every id on `origin/main`.
- I2: the branch's derivation over the live store and state, compared task by task with the served
  `site/status.json`; the only differences are the expected ones.
- Every new `depends_on` pattern matches a file at `connect-automate`'s and `document-ocr`'s heads
  (contract 08's mapping check).

Settle, after merge: the first tick shows:
- the new conditions as `no_evidence`;
- `connect-automate` and `document-ocr` head rows;
- the bundle's issue gate reading the six milestones, or **unavailable** while `document-ocr` has none;
- no new source failure.

## Decisions

D1. **The automation set is named once, and the bundle carries it by sharing checks.**
- Sharing checks is how the bundle already carries the connect handoffs and app releases (eleven shared
  checks today), and one demonstration record then satisfies both rows (settling test 4).
- A new "task depends on task" rule would put cross-task logic into `rules.py`, which today derives each
  task from its own conditions alone.
- The validator gives the same guarantee: drift is a load error, and nothing in the rules changes.

D2. **Bundle-level checks are anchored on `connect-contracts`, with all five product repositories as
participants.**
- These demonstrations are about the bundle, not one app. `connect-contracts` holds what the bundle
  shares: entitlement v1, and the canonical runtime profile of MODEL-SETUP revision 8 MS-SHARE-5.
- When the installer's or the model host's repository joins the catalogue, these checks add it as a
  participant.
- Their fingerprints then change, and earlier demonstrations read "configuration changed". That is right,
  because they were not bound to that code.

D3. **`connect-automate` joins the catalogue for its issue gate (revisits contract 08 D3).**
- Contract 08 deferred it because nothing needed its head.
- The first release now includes the automations, and a release-blocking bug in the automation engine is
  filed where that code lives.
- Evidence binding is unchanged: the Email Watcher pin is still what released behaviour runs.

D4. **The bundle download's publication condition waits for its repository.**
- The installer's repository follows MODEL-SETUP Q4 and #94 release step 6, and neither exists yet.
- A `release_artifact` needs a repository to read releases from, and pointing it at a stand-in would be
  false.
- Until then, "released" still needs "ready", which needs `rel.bundle_download_*`.
- The release session adds the publication condition in the same change that adds the installer
  repository.

D5. **The single-app runtime claim is on each app's row.** An app releases on its own (B1), so each app must
prove it. One bundle-level "any app alone" demonstration would prove it for one app, not three.

D6. **Two licence demonstrations per platform.**
- The online behaviour waits on the licence service, whose owner is undecided (#94).
- The behaviour without a licence, and the offline licence, do not wait: entitlement v1 and the
  hand-signed licence exist today.
- Separate conditions let the page show which half is waiting.

D7. **`manual.ew_live_calendar`'s note says "on the installed app".**
- A note is a prose key, so editing it keeps evidence identity.
- The store holds no record for this check (checked 2026-09-27: installed-demo rows exist only for the six
  app install checks and `manual.ip_removal_test`), so no earlier observation is re-described.

D8. **Automated tests for automations 2 and 3 are required once their tasks name them.**
- B6 cannot require an automated condition today without naming tests that do not exist. Codex's
  contracts (#94, codex step 4) add them.
- When their checks are added to the automation tasks, B6 c makes the bundle row carry them.
- Until then, the bundle's bar for those two automations is their installed demonstrations on both
  platforms.

D9. **Without a licence, both licensed features stop (the operator's answer, #94).**
- One-click handoffs (`connect.capability_exchange`) and the automations (`connect.automations`) stop.
- Standalone use and user data always keep working, and standalone use includes reading a scanned PDF
  (D11).
- The entitlement format does not change. Exempting local OCR requests from the Connect entitlement is
  codex's ADR (#94, codex step 5).

D10. **Privacy destinations are the operator's wording (#94).** The condition checks the capture against the
destination list in the operator's wording. That list covers model downloads and the software that runs
them (our llama.cpp build and NVIDIA's CUDA libraries), updates, and the licence service.

D11. **Reading a scan is part of the shared runtime and needs no licence (the operator's decision, #94).**
- The bundle download installs the OCR provider, and so does a single app that needs it (B3, B4).
- Revision 1 had left OCR out entirely. The review of revision 1 found the gap: without OCR, automations 2
  and 3 cannot read a scanned bill, and standalone use cannot read one without a licence.

D12. **`document-ocr` is a participant wherever a demonstration reads a scan.** The OCR provider's
revision decides whether a scan is read, so a change to it must reopen those demonstrations. Email
Watcher's shared-runtime demo reads no scan, so it does not name `document-ocr`.

D13. **Windows demonstrations wait for OCR on Windows; they are not weakened.**
- The first release is Linux and Windows (#94), and `document-ocr` is Linux-only today.
- A Windows demonstration that skipped the scan would prove less than its Linux twin. Instead, it waits
  for codex's Windows port.

D14. **A private repository on the page.** `document-ocr` is private, as `invoice-processor` is. Its head,
commit subjects and release-milestone issue titles show on the page the same way. Commercial terms must
therefore stay out of both repositories' commit subjects and First Public Release issue titles too.

## Estimated diff

- `catalogue.json`: ~420 lines (21 new checks, 29 new conditions, two repositories, release block).
- `catalogue.py`: ~40 lines.
- Tests: ~210 lines.
- `RELEASE_CONTRACT.md`: ~45 lines.
- README: ~10 lines.
