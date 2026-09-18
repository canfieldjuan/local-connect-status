# Contract 01 — Cross-app acceptance stages the installed Connect entitlement

Status: **proposed** (review before any code). Slice 1 of the 2026-09-18 fix plan.
Scope: `lcstatus/verify.py` (`Runner.accept_ew_ip` and two small helpers), `tests/test_verify_parsing.py`,
one README paragraph. Nothing in `catalogue.json`, `rules.py`, `evidence.py`, or any product repository.

## Problem (evidence, not description)

`xapp.accept_ew_to_ip` has recorded `fail` on every run since 2026-09-12T17:46:02Z — 32 consecutive
records, the latest (`2c12351289d1cd632d0fc85e`) at the current heads (Invoice Processor `ddbb4966`,
Email Watcher `f3aa24b6`, contracts `2d3ec8fb`). The dashboard therefore shows
`connect.handoff_bill_to_ledger` and `release.local_connect_bundle` as **check failed**. That verdict is
produced by this harness, not by the products:

- The runner rebuilds an isolated `HOME` that contains only `Desktop/invoice-processor` and
  `Desktop/connect-contracts` symlinks (`lcstatus/verify.py:527-537`) and launches the acceptance
  script with `HOME` pointed at it (`:556-561`). `sanitized_python_env` starts from `os.environ`
  (`:75`), so every inherited `XDG_*` variable still reaches the script.
- The Email Watcher at `f3aa24b6` locates the licence at
  `$XDG_CONFIG_HOME/local-connect/entitlement-v1.json`, else `$HOME/.config/local-connect/entitlement-v1.json`
  (`src/eom_email_watcher/entitlement.py:296-310`, called from `:176-180`), and answers `MISSING` when
  the file is absent (`:199-202`). The isolated `HOME` has no `.config` at all.
- The Invoice Processor script at `ddbb4966` stops on that answer: `STOP  the watcher does not consider
  this installation entitled`, return 1 (`scripts/accept_against_email_watcher.py:114-118`). The run's
  log (`data/logs/xapp.accept_ew_to_ip.ddbb4966-f3aa24b6.log`) shows exactly that sequence.
- The same product revisions passed before isolation existed: record index 250
  (`2026-09-12T04:03:11Z`, EW `11ff9306` / IP `41184456`, `detail` without `isolated_home`) is `pass`;
  index 277 (`2026-09-12T17:46:02Z`, identical EW/IP revisions, `detail` with `isolated_home`) is `fail`.
  Those passes consumed the operator's installed licence at `~/.config/local-connect/entitlement-v1.json`
  (0600, key `local-connect-prod-2026-01`, valid 2026-09-08T16:47:28Z → 2027-09-03T01:36:59Z).

Fixtures cannot replace that licence without changing a product: the watcher pins the production
authority (`APPROVED_RELEASE_AUTHORITIES`, `entitlement.py:44-52`, enforced for installed keyrings at
`:339-340`) and prefers the *bundled* keyring (`:172-174`), which the script stages from the exact
contracts tree's **release** keyring under `sys._MEIPASS` (`accept_against_email_watcher.py:35-41`).
The condition's own claim says "with the exact contracts keyring". The contracts fixture keyring
(`entitlements/v1/fixtures/test-keyring.json`) is therefore not an option here and is not proposed.

## Observable behaviour

B1. **Host licence resolution.** Before launching the script, the runner resolves the *collector's*
installed licence path the way the product does on Linux: `$XDG_CONFIG_HOME/local-connect/entitlement-v1.json`
when `XDG_CONFIG_HOME` is set and absolute, otherwise `$HOME/.config/local-connect/entitlement-v1.json`,
using the environment the collector itself runs in (before any isolation).

B2. **Pre-check (structure and window only, never the signature).** The file must be a regular file of
1–16384 bytes (the product's own `MAX_ENTITLEMENT_BYTES`), a JSON object with `format_version == 1`, a
string `key_id`, and a `payload_base64url` that decodes (base64url, missing padding tolerated) to a JSON
object whose `not_before` and `expires_at` parse as timezone-aware instants with
`not_before <= now < expires_at` (`now` = the collector's UTC clock). Signature validity is the product's
job and is not evaluated.

B3. **Staging.** The runner creates `<compat_home>/.config` (0700), `/local-connect` (0700) and writes
`entitlement-v1.json` (0600) with the host file's exact bytes. It also creates `<compat_home>/.local/share`,
`.cache` and `.local/state` (0700) so the XDG overrides in B4 resolve to existing directories.

B4. **Environment.** In addition to today's settings (`HOME=<compat_home>`, `PYTHONPATH=<ew_tree>/src`,
`ACCEPTANCE_DIR`, `ACCEPTANCE_MODEL` removed, `PYTHONHOME`/`PYTHONPATH` sanitised, `PYTHONNOUSERSITE=1`)
the script runs with `XDG_CONFIG_HOME=<compat_home>/.config`, `XDG_DATA_HOME=<compat_home>/.local/share`,
`XDG_CACHE_HOME=<compat_home>/.cache`, `XDG_STATE_HOME=<compat_home>/.local/state`. `XDG_RUNTIME_DIR`
is left as inherited: the pre-isolation passes ran with it inherited and the script manages the
provider's runtime directory itself (`accept_against_email_watcher.py:131-133`); changing it is out of scope.

B5. **Cleanup.** The staged licence file is removed in the runner's `finally`, on every exit path
(pass, fail, isolation failure, timeout, `OSError`). The rest of `<compat_home>` is left as today (it is
rebuilt from scratch on the next run, `verify.py:529-533`).

B6. **Record.** `detail` gains `entitlement_source` (the host path as a string), `entitlement_key_id`,
`entitlement_not_before`, `entitlement_expires_at` — never the payload or signature. When the script's
stdout contains a line beginning `watcher entitlement decision`, its trailing value is recorded as
`detail.watcher_decision` (diagnostic only; it never changes the verdict). `command`, `summary`
(last stdout line, 200 chars) and every existing `detail` key are unchanged.

B7. **Verdicts.** Pre-check failures return `verdict="unavailable"` with one of these summaries:
`no installed Connect entitlement at <path>`, `installed Connect entitlement unreadable: <reason>`,
`installed Connect entitlement not valid before <iso>`, `installed Connect entitlement expired at <iso>`.
Nothing else changes: return code 0 → `pass`; 97 → `unavailable` (isolation failure); any other code →
`fail`; timeout / `OSError` → `unavailable`. `unavailable` results already count as source failures
(`collect.py:64-70`), so a missing or expired licence turns the run red and is listed in the banner.

B8. **Fingerprints.** `catalogue.json` is not edited, so the check and condition fingerprints are
unchanged and the first passing record at the current heads reads **verified** without any other change.

## Invariants

I1. Nothing under `.cache/trees/**` (the exact product trees) is created, modified or deleted by this
slice, and the host licence is only ever opened for reading.
I2. `Path.home()` inside the script still resolves to `<compat_home>`; the `Desktop/*` symlinks and the
`/tmp/watcher-main` handling are unchanged.
I3. The staged copy is byte-identical to the host file and never outlives the run.
I4. Only the four XDG base directories in B4 are added to the environment; everything else is as today.
I5. No fixture keyring, fixture licence, or substitute keyring is ever staged (see Problem).
I6. The pre-check can only turn a would-be false `fail` into `unavailable`; it can never produce `pass`.
I7. AGENTS.md holds: no product repository or developer checkout is read or written.

## Failure cases

| Situation | Result |
|---|---|
| Host licence absent | `unavailable`, `no installed Connect entitlement at <path>`; script not launched |
| Host licence not a regular file, empty, > 16384 bytes, not JSON, wrong `format_version`, missing `key_id`/`payload_base64url`, payload undecodable, timestamps missing/naive | `unavailable`, `installed Connect entitlement unreadable: <reason>`; script not launched |
| `now < not_before` | `unavailable`, `… not valid before <iso>`; script not launched |
| `now >= expires_at` | `unavailable`, `… expired at <iso>`; script not launched |
| Licence present and in window but the product rejects it (bad signature, key not in keyring, feature missing) | script returns 1 → `fail`; `detail.watcher_decision` carries the product's reason. Documented limitation: indistinguishable from a product regression without product crypto; the log and `watcher_decision` make it visible to a person |
| Staging directory cannot be created / copy fails (`OSError`) | `unavailable`, `installed Connect entitlement unreadable: <reason>`; script not launched |
| Script timeout / cannot start | unchanged (`unavailable`); staged copy still removed (B5) |
| Inherited `XDG_CONFIG_HOME` points elsewhere | ignored by the script (B4 overrides it); used only for B1 on the host side |

## Concurrency model

Unchanged. `WATCHER_COMPAT_LOCK` (`verify.py:540-548`) already serialises acceptance runs on this
machine; `<compat_home>` is per-participant-key and rebuilt each run; the staged file lives inside it.
The collector's `data/.lock` prevents two collections. The host licence is read once at run start; a
rotation during the run is irrelevant to that run.

## Settling test evidence

Unit (all in `tests/test_verify_parsing.py`, using a stub script written by the test — no product code):

1. `test_cross_app_runner_uses_all_exact_trees_and_isolated_home` (rewritten; the stub currently only
   `raise SystemExit(0)` and could not detect this failure): the stub asserts that
   `$XDG_CONFIG_HOME/local-connect/entitlement-v1.json` exists with mode 0600 and the host bytes, that
   `Path.home()` is the isolated home, that `XDG_DATA_HOME`/`XDG_CACHE_HOME`/`XDG_STATE_HOME` are under it,
   and exits 0 → `pass`, `detail.entitlement_key_id` present.
2. `test_cross_app_runner_records_unavailable_without_installed_entitlement`: no host licence →
   `unavailable`, summary starts `no installed Connect entitlement`, stub never ran.
3. `test_cross_app_runner_records_unavailable_for_expired_or_not_yet_valid_entitlement`: two host files
   built by the test (unsigned payloads with past `expires_at` / future `not_before`) → `unavailable`
   with the respective summary; stub never ran.
4. `test_cross_app_runner_records_unavailable_for_malformed_entitlement`: oversize, non-JSON, naive
   timestamp → `unavailable … unreadable: …`.
5. `test_cross_app_runner_inherited_xdg_config_home_does_not_leak`: `XDG_CONFIG_HOME` set to a directory
   holding a *different* licence → the host resolution (B1) uses it, the stub sees only the staged copy under
   the isolated home.
6. `test_cross_app_runner_removes_staged_entitlement_on_every_exit`: after pass, fail, exit 97 and a
   simulated `TimeoutExpired`, the staged file is gone.
7. `test_cross_app_runner_records_watcher_decision_line`: stub prints
   `watcher entitlement decision                missing` then exits 1 → `fail`,
   `detail.watcher_decision == "missing"`.

Live (after merge and fast-forward of the live tree): the next timer tick's journal shows
`running xapp.accept_ew_to_ip ...` followed by `pass:`; `site/status.json` shows
`bill.cross_app_acceptance` and `rel.bundle_invoice_contract` as **verified** at the current heads with a
record whose `detail.entitlement_key_id == "local-connect-prod-2026-01"`. If the live tick instead yields
`fail` with a product-side `watcher_decision`, that is a genuine product finding and this slice has still
done its job: the verdict is then product-caused.

## Decisions

D1. **Copy one file rather than point `XDG_CONFIG_HOME` at the operator's real `~/.config`.** The
latter would expose the whole config tree to exact-tree product code and let it write there; copying the
single licence keeps the isolation the runner exists for.
D2. **`unavailable`, not `fail`, for pre-check failures.** README already defines an environment that
cannot run a check as "check skipped or unavailable"; an absent or expired licence is that, and it must
still make the run red (source failure) rather than look like product evidence either way.
D3. **No catalogue note edit in this slice.** `check_fingerprint` hashes the whole check including
`note` (`evidence.py:62-65`); editing it would invalidate every existing record for this check. The note
is rewritten in slice 3 once fingerprints are semantic.
D4. **Fixtures deferred to the products.** If a fixture-based acceptance is wanted, it is a product
change (a keyring/licence override in the IP script plus a non-production authority in the watcher's
test path) and belongs in those repositories' issue trackers, not here.

## Estimated diff

~120 lines in `lcstatus/verify.py`, ~170 lines of tests, one README paragraph.
