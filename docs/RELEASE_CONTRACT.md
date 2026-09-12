# First public release contract

The three apps may become release candidates independently. Local Connect becomes a bundle release only after the released apps also complete their installed handoffs. Automate rules and scheduled workflows are planned for later releases and do not block the first public release.

## Status gates

Each app has its own release row in `catalogue.json`:

- **Email Watcher** requires its exact-revision primary CI, Linux and Windows package/build evidence, and an installed primary-flow demonstration on each platform.
- **Document Summarizer** requires its exact-revision Rust/frontend CI and an installed cited-summary demonstration on each platform.
- **Invoice Processor** requires its exact-revision primary, packaging, and first-run model checks, plus an installed primary-flow and persistent-data demonstration on each platform.

An app is **ready for release** when every automated check and installed demonstration in its row is current, Linux and Windows are both satisfied, and its issue gate is clear. It becomes **released** only when the current revision also has a published GitHub release with the required Linux package, Windows installer, and checksums.

The **Local Connect bundle** additionally requires exact-tree PDF and invoice handoff tests, durable busy retry, local entitlement verification in all three apps, installed PDF and invoice handoffs on Linux and Windows, and uninstall demonstrations that remove provider discovery. It becomes **released** only after all three current app revisions are also publicly released.

## Issue gate

`First Public Release` is the single blocking milestone in each product or contracts repository. Every open issue in that milestone blocks the affected app release. The bundle row combines milestone issues from all four repositories.

Issue queries produce append-only issue-gate records at the current repository revision. They are evidence only of whether the release gate is clear, never evidence of product capability and never a completion percentage. Closing an issue removes the blocker on the next recorded query; it does not satisfy a test, installed demonstration, platform, or publication condition. An issue API failure records an unavailable gate and blocks readiness instead of looking like an empty issue list.

Issues outside this milestone remain visible in GitHub but do not block the first release. Put an issue in the milestone when it is a realistic first-release failure in one of these classes:

- security or privacy;
- data loss, corruption, or incorrect money handling;
- installer, startup, licence, or primary-flow failure;
- a buyer-facing claim that the current app does not meet.

Polish, broader hardening, and Automate work belong after the first release unless they produce one of those failure classes. This is the point where the release arc stops expanding: satisfy the named evidence, clear the milestone, publish, and move remaining work to later milestones.

## Candidate revisions

Every automated result, installed observation, and release artifact is bound to an exact repository revision. A later commit makes earlier proof historical until the relevant check or demonstration is repeated. Cross-app evidence records every participating repository revision, so a change in any participant reopens that condition.

Windows installed observations include the chosen distribution/signing path. The dashboard records the observed result; the operator owns the channel decision.
