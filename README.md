# Local Connect status

An internal dashboard that says, in plain language, what Email Watcher, Document Summarizer
and Invoice Processor can do today, what Connect and Automate add, and what still stands
between here and a Linux + Windows release — with dated evidence behind every row.

It exists because a merged PR reads like "done" and usually isn't. Here a merge only ever
means "check again".

## How it works

```
GitHub default branches ──poll──▶ bare mirrors (.cache/mirrors)
                                        │
                        head moved? ──▶ change record: which tasks it touches, which files nobody mapped
                                        │
              checks named in catalogue.json ──▶ runners ──▶ data/records.jsonl  (append-only)
              · pytest at the exact revision (JUnit-counted, exit code decides)
              · GitHub Actions jobs for that exact sha (per platform)
              · cross-app acceptance: exact Email Watcher + Invoice Processor + contracts trees
              · GitHub Releases
              · human observations (scripts/record_observation.py)
                                        │
                     rules.py ──▶ site/status.json, site/report.md, site/dashboard.html  (atomic)
```

Three things are kept apart on purpose:

- **`catalogue.json`** — product intent. Stable task ids, the promise, the acceptance
  conditions, which code they depend on, which platforms count. Edited by a person.
- **`data/records.jsonl`** — evidence. Every check that ran, against which exact revision, on
  which platform, how it ended. Written only by runners and the observation script.
- **`site/`** — the current report, derived from the two above. Never edited.

## Status rules

A condition is **verified** only when passing evidence *of its own kind* exists at the
*current* revision (every participant's revision, for cross-app checks). Older passing
evidence stays visible as **changed since verification**. A failing check at the current
revision is **check failed**. A record that exists but carries no result (the check was
skipped, unavailable, pending or unknown) is **check skipped or unavailable**. When no record
of the right kind exists at all the condition, platform or task reads **no evidence** — it
never reads as if something had been checked. Source inspection is **needs verification** —
a string in the code is a hint, not proof, and a missing string is not proof of absence.

Maturity never exceeds the evidence: `planned` → `partly built` → `built` (all test/CI
conditions verified) → `demonstrated` (plus installed-app observations) → `ready for
release` (plus every required platform and release check) → `released` (a published GitHub
Release recorded for that repository). Linux and Windows are scored separately; Linux alone
never means ready.

Consecutive identical observations are stored once. If a result changes and later returns
to an earlier value, that recovery remains a separate observation and becomes the latest result.

The exact pytest verdict rule: a nonzero exit is `fail` whatever the output said; zero
executed tests is `skip`; counts come from JUnit XML, not from the summary line.

**The interpreter is part of the verdict.** `catalogue.json` declares the CPython version a
repository is verified under (`repos.<name>.python`), `uv sync` is pinned to it, and every
pytest record's command starts with the interpreter that ran it, e.g. `[Python 3.13.12]`.
Without the pin uv takes the newest CPython installed, and the same Invoice Processor
revision read `1 failed` under 3.14.3 and green under 3.13.11 for a test that asserts
`json.loads` exhausts recursion on 12,000 nested brackets (true on 3.11-3.13, not on 3.14;
filed as invoice-processor#37). A verdict has to be comparable across revisions.

## Running it

```
python3 -m lcstatus.collect                  # poll, verify, render (routine)
python3 -m lcstatus.collect --heavy          # also run Document Summarizer's Rust suite (slow)
python3 -m lcstatus.collect --checks ip.pytest.ledger_via_connect xapp.accept_ew_to_ip
python3 -m lcstatus.collect --render-only    # re-render from stored records
python3 scripts/record_observation.py --help # record a human demonstration
uv run pytest                                # this project's own tests
```

Exit status 0 means every source was read; 2 means something could not be read and the
report says which. A failed fetch or GitHub query never looks like a clean empty result.

## Where it runs

Installed as **user systemd units** (`systemd/install.sh`), matching the other Local
Connect services on this machine:

- `local-connect-status.timer` — every 20 minutes, persistent across sleep, runs the collector.
- `local-connect-status-web.service` — serves `site/` on `http://127.0.0.1:8790/`, loopback only.

```
systemd/install.sh              # install or refresh, enable, start
systemd/install.sh --uninstall  # stop and remove
systemctl --user status local-connect-status.timer local-connect-status-web.service
journalctl --user -u local-connect-status.service -n 50
```

GitHub is read with the `gh` CLI using its stored login; no token is written anywhere and
the dashboard is static HTML with the data embedded, so the browser never holds a credential.

**Missed events** recover automatically: the collector compares each repository's current
head to the last head it recorded, so a merge that landed while the machine was off is seen
on the next tick, with every intermediate commit listed in the change record.

**Heavy checks** (the Rust suite) are opt-in. Document Summarizer's routine evidence is its
GitHub Actions run for the exact sha; a local run is for when that isn't enough.

## What it cannot do

- Run a human demonstration. The Invoice Processor removal test needs `sudo dpkg`; the
  calendar write needs a real Microsoft tenant. A person runs those and records the result
  with `scripts/record_observation.py`, which requires `--observed-by` and an artifact.
- Touch a GPU. The cross-app acceptance runs with the stand-in model only.
- Decide launch scope. Which Automate tasks the first release requires is recorded as
  `undecided` in the catalogue until a person changes it.

## What the tests prove

`uv run pytest` runs the reporting project's own suite. Each required failure case maps to a
test that would fail if the dashboard could be fooled that way:

| The dashboard must never… | Test |
|---|---|
| treat a process that printed "passed" but exited non-zero as passing | `test_exit_code_beats_summary_line`, `test_a_process_can_print_passed_and_still_fail` |
| count zero executed tests as proof | `test_zero_executed_is_not_proof` |
| let evidence from an older revision satisfy the current one (it stays visible as "changed since verification") | `test_old_revision_evidence_is_changed_since_and_keeps_last_proven` |
| let a late result about an old revision displace a newer one | `test_late_result_for_old_revision_does_not_displace_newer` |
| accept a cross-app demonstration when one participant has moved | `test_cross_app_demo_with_one_stale_participant_is_changed_since` |
| let a passing helper test satisfy an installed-demonstration condition | `test_passing_test_cannot_satisfy_demo_condition` |
| promote anything on a source-string match, or treat a missing string as proof of absence | `test_source_inspection_is_inconclusive_and_never_raises_maturity`, `test_code_change_maps_to_task_and_marker_rename_alone_cannot_prove_removal` |
| let Linux evidence stand in for Windows | `test_linux_only_evidence_leaves_windows_not_checked_and_blocks_release_readiness` |
| show pending / unavailable / partial as green | `test_non_results_are_not_checked` |
| call something released without a published release | `test_release_requires_release_artifact_not_just_demos`, `test_release_absence_is_an_explicit_not_met_at_current_head` |
| duplicate progress on a repeated delivery, lose a recovery, or lose a distinct change record | `test_duplicate_delivery_stores_once`, `test_pass_fail_pass_recovery_is_retained_and_wins_after_reload`, `test_change_records_from_different_baselines_are_both_kept` |
| let a docs-only or contract-only change look like running functionality | `test_docs_only_change_is_flagged_and_unmapped_files_surface` |
| show a failed fetch, missing repository or unavailable GitHub as a clean empty result | `test_missing_repository_and_unavailable_github_are_explicit` |
| invent data when only rendering, or re-promote evidence after a head became unknown | `test_render_only_never_invents_data`, `test_render_only_excludes_head_marked_unknown_and_does_not_repromote_old_pass` |
| label a task with nothing checkable as "verified at current code" | `test_task_with_only_inspection_conditions_is_not_checked_not_current` |
| let another repository's release satisfy an app condition, or dispatch an app release check across every repository | `test_foreign_repository_release_record_cannot_satisfy_app_condition`, `test_release_dispatch_is_scoped_to_the_configured_repository` |
| prefer an older workflow rerun over a newer workflow run | `test_latest_distinct_workflow_run_beats_older_rerun_attempt` |
| delete an unowned compatibility path or read cross-app fixtures from developer checkouts | `test_prepare_owned_symlink_refuses_real_directory_without_deleting_it`, `test_prepare_owned_symlink_refuses_foreign_symlink`, `test_cross_app_runner_uses_all_exact_trees_and_isolated_home` |
| hide unavailable Actions or Releases sources, or crash on an environment timeout | `test_unavailable_actions_and_release_results_are_run_failures`, `test_uv_sync_timeout_becomes_an_explicit_failure` |
| publish stale status copy or embed a record that terminates the dashboard script | `test_next_action_is_derived_from_condition_state_not_catalogue_copy`, `test_dashboard_json_cannot_terminate_its_script_and_footer_is_evidence_driven` |

Two runs cannot interleave: the collector takes an exclusive lock on `data/.lock`, and a
baseline write waits for a running collection to finish rather than racing it.

## Live demonstration (2026-09-09)

The reporting loop was exercised end to end through the installed units, not by hand. The
collector's recorded heads for Email Watcher and Document Summarizer were set back to
revisions genuinely observed the previous day (`41267c6`, `4a59431`), then
`local-connect-status.service` was started with `systemctl --user start`. From its journal:

```
Starting local-connect-status.service - Local Connect status collection (poll default branches, verify, render)...
running ew.pytest.unit @ 29fd046f7ba1 ...
  pass: 1107 passed, 15 skipped, 1 deselected in 52.71s
running ew.pytest.queue_retry @ 29fd046f7ba1 ...
  pass: 2 passed in 0.74s
running ip.pytest.all @ 74f8bca0d1e0 ...
  pass: 611 passed, 7 skipped in 121.51s (0:02:01)
running ip.pytest.ledger_via_connect @ 74f8bca0d1e0 ...
  pass: 3 passed in 1.31s
running xapp.accept_ew_to_ip ...
  pass: registration after shutdown                 gone
rendered .../site (41 records; 0 source failures)
Finished local-connect-status.service
```

It recorded the real default-branch move `eom-email-watcher 41267c607c5c -> 29fd046f7ba1: 10
files` — the merges of #120, #121 and #122 — mapped it to five tasks, flagged seven files no
task claims as needing assessment, and re-verified the affected conditions at the new head.
The "An emailed bill appears in my ledger" row went from verified at the old head, through
"changed since verification", back to verified at `29fd046` in one tick, while its
installed-app demonstration (recorded at `5b438ba` / `c3b5cf6`) correctly stayed "changed
since verification" because nobody has re-run it.

Also recorded during bring-up, and left in the store on purpose: a change record with a
fabricated baseline (`41267c6b4fd8`, `0 files`) from a run where the operator-supplied sha was
wrong. It is the reason change records now carry their origin in their identity.
