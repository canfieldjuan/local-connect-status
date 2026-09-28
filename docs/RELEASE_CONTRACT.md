# First public release contract

The product is the **Local Connect bundle**: one download for Linux and Windows that installs the three apps, the shared runtime (the model host and the OCR provider) and one copy of the model. Each app may also be released on its own, and installing one app alone sets up the shared runtime if it is absent. The three cross-app automations are part of the first release. Commercial terms are private until launch; this document refers to them only as "the licensing model in invoice-processor#94 (private)". Contract 09 (`docs/contracts/09-bundle-release.md`) is the detailed definition.

## Status gates

Each app has its own release row in `catalogue.json`:

- **Email Watcher** requires its exact-revision primary CI, Linux and Windows package/build evidence, and an installed primary-flow demonstration on each platform.
- **Document Summarizer** requires its exact-revision Rust/frontend CI and an installed cited-summary demonstration on each platform.
- **Invoice Processor** requires its exact-revision primary, packaging, and first-run model checks, plus an installed primary-flow and persistent-data demonstration on each platform.

Each app row also requires, on each platform, that the app installed alone sets up the shared runtime with no configuration, and reuses an existing one with no second model copy. For Document Summarizer and Invoice Processor, that includes the OCR provider and reading a scanned PDF without a licence.

An app is **ready for release** when every automated check and installed demonstration in its row is current, Linux and Windows are both satisfied, and its issue gate is clear. It becomes **released** only when the current revision also has a published GitHub release with the required Linux package, Windows installer, and checksums.

The **Local Connect bundle** additionally requires exact-tree PDF and invoice handoff tests, durable busy retry, local entitlement verification in all three apps, installed PDF and invoice handoffs on Linux and Windows, and uninstall demonstrations that remove provider discovery. It also requires, installed on Linux and on Windows:

- **the three automations**, each demonstrated with every automated check its task names. `release.automate_scope.tasks` names them, and the catalogue refuses to load if the bundle row stops carrying any of their proofs. Automations 2 and 3 include a scanned PDF read through OCR;
- **the bundle download**: the three apps, the model host, the OCR provider and exactly one model copy, with one server shared by every app;
- **the licence**: online behaviour as the licensing model in invoice-processor#94 (private) specifies; without a valid licence, standalone use, user data and reading a scanned PDF keep working while one-click handoffs and the automations stop plainly; a hand-signed offline licence restores them without going online;
- **the privacy promise**: the user's emails, invoices and documents are processed on their computer and never uploaded. A network capture during the automation and licence demonstrations shows only the destinations the promise in #94 allows, and no document content.

A demonstration that reads a scan binds the `document-ocr` revision as well. The bundle becomes **released** only after all three current app revisions are publicly released and its own download is published; that publication condition is added together with the installer's repository.

## Issue gate

`First Public Release` is the single blocking milestone in each product, runtime or contracts repository. Every open issue in that milestone blocks the affected app release. The bundle row combines milestone issues from all six repositories: the three apps, `connect-contracts`, `connect-automate` and `document-ocr`.

Issue queries produce append-only issue-gate records at the current repository revision. They are evidence only of whether the release gate is clear, never evidence of product capability and never a completion percentage. Closing an issue removes the blocker on the next recorded query; it does not satisfy a test, installed demonstration, platform, or publication condition. An issue API failure records an unavailable gate and blocks readiness instead of looking like an empty issue list.

Issues outside this milestone remain visible in GitHub but do not block the first release. Put an issue in the milestone when it is a realistic first-release failure in one of these classes:

- security or privacy;
- data loss, corruption, or incorrect money handling;
- installer, startup, licence, or primary-flow failure;
- a buyer-facing claim that the current app does not meet.

Polish and broader hardening belong after the first release unless they produce one of those failure classes. This is the point where the release arc stops expanding: satisfy the named evidence, clear the milestone, publish, and move remaining work to later milestones.

## Candidate revisions

Every automated result, installed observation, and release artifact is bound to an exact repository revision. A later commit makes earlier proof historical until the relevant check or demonstration is repeated. Cross-app evidence records every participating repository revision, so a change in any participant reopens that condition.

Windows installed observations include the chosen distribution/signing path. The dashboard records the observed result; the operator owns the channel decision.
