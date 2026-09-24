# Local Connect status

An internal dashboard that says, in plain language, what Email Watcher, Document Summarizer
and Invoice Processor can do today, what Connect and Automate add, and what still stands
between here and a Linux + Windows release — with dated evidence behind every row.

It exists because a merged PR reads like "done" and usually isn't. Here a merge only ever
means "check again".

## How it works

```
GitHub default branches ──poll──▶ bare mirrors (.cache/mirrors)
GitHub First Public Release issues ──poll──▶ issue-gate runner
                                        │
                        head moved? ──▶ change record: which tasks it touches, which files nobody mapped
                                        │
              checks named in catalogue.json ──▶ runners ──▶ data/records.jsonl  (append-only)
              · pytest at the exact revision (JUnit-counted, exit code decides)
              · GitHub Actions jobs for that exact sha (per platform)
              · cross-app acceptance: exact Email Watcher + Invoice Processor + contracts trees
              · GitHub Releases
              · open release-milestone issues at the exact repository revision
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
revision is **check failed**. A failure at an earlier revision that has not been re-run since is
**failed at an earlier revision, not re-run** — nothing is known about the current code until the
check runs. Every one of those states compares revisions, so none of them is asserted unless the
repository's current revision (every participant's, for cross-app checks) was observed this tick; when
a head lookup failed, the condition reads **current revision not observed this tick**, the task reads
**current revision not observed**, the last result stays visible, and the next action is to restore
repository visibility. A record that exists but carries no result (the check was
skipped, unavailable, pending or unknown) is **check skipped or unavailable**. When no record
of the right kind exists at all the condition, platform or task reads **no evidence** — it
never reads as if something had been checked. Source inspection is **needs verification** —
a string in the code is a hint, not proof, and a missing string is not proof of absence.

The release issue gate reads clear only after its milestone was found in the repository and the open-issue
listing was read; a missing or renamed milestone or a failed lookup is **unavailable**, never clear. The
listing decides; GitHub's milestone counter is recorded when it disagrees and never changes the verdict.

Maturity never exceeds the evidence: `planned` → `partly built` → `built` (all test/CI
conditions verified) → `demonstrated` (plus installed-app observations) → `ready for
release` (plus every required platform and a clear issue gate) → `released` (a published
GitHub Release recorded for the current repository revision). Linux and Windows are scored
separately; Linux alone never means ready. Email Watcher, Document Summarizer, and Invoice
Processor have independent release rows; the Local Connect bundle has its own cross-app row.

Open issues in the `First Public Release` milestone are recorded in `data/records.jsonl` and
block the affected release row without changing any capability result. Closing an issue removes
a blocker but proves nothing about product behavior by itself. An unavailable issue query records
an unavailable gate and fails closed. The complete boundary, including which hardening can move
after launch, is in [`docs/RELEASE_CONTRACT.md`](docs/RELEASE_CONTRACT.md).

Consecutive identical observations are stored once. If a result changes and later returns
to an earlier value, that recovery remains a separate observation and becomes the latest result,
even when GitHub run IDs, URLs, or other per-attempt metadata differ. Every execution writes its own
log files, named with the execution's start instant; when the store collapses an execution as an
identical observation its files are removed, so `data/logs` holds exactly one log set per stored
record and a stored record's log is never overwritten. A pytest record's `command` omits the JUnit
path (the file is the log's sibling), so identical executions still collapse. Files written before
2026-09-19 had one fixed name per check and revision and may hold the output of a later execution at
the same revision. All evidence timestamps are
compared as absolute instants across timezone offsets. Manual
observations validate and normalize their observation time, so a backfilled older result cannot
displace a newer observation.

Evidence carries fingerprints of both the complete catalogue check configuration and the condition
claim it was meant to prove. The original pre-fingerprint JSONL prefix is supported without
rewriting evidence: the store first authenticates that exact byte prefix, then attaches its
historical check and condition fingerprints only in memory. Prose is not configuration: a
check's `note` is left out of its fingerprint, and fingerprints written before that rule are
mapped through a frozen alias table, so editing a note never orphans the evidence it describes.
A changed configuration or claim stays visible: when nothing has been admitted under the current
one, an earlier pass reads **configuration changed since verification** until the check runs
again under the current configuration. That state is history, not proof — it never verifies,
never counts toward maturity and never clears a release gate. Fingerprint-free rows outside the
authenticated prefix are not admitted through the compatibility path. A cross-app record is admitted
only when it names exactly the check's declared participants; every check names one repository, and
the rules never substitute another. The store refuses to write a row whose instants are not
timezone-aware; the collector records that refusal as a source failure and continues.

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
python3 -m lcstatus.collect --heavy          # also run Rust and PDF cross-app checks (slow)
python3 -m lcstatus.collect --checks xapp.accept_ew_to_ip xapp.accept_ew_to_ds
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
  Each tick observes heads and reads CI runs, releases and release issues. A local check runs **once
  per revision**: when the store already holds a decided result — a pass, an inspection's reading, or a
  failure the test framework itself counted — for that exact check, its conditions, the catalogue entries
  of the repositories it touches, the collector's own code and the revision(s), the tick prints
  `unchanged` and does not re-run it. Any other result (a failure with no framework count, a skip, an
  unknown or unavailable result) is retried every tick. A change to the collector's code runs every
  local check once; a tick that finds its own code changed on disk after it started exits without
  running. The host environment is not in that key: `--rerun` or `--checks` forces execution.
- `local-connect-status-web.service` — creates and serves generated `site/` on
  `http://127.0.0.1:8790/`, loopback only.

```
systemd/install.sh              # install or refresh, enable, start
systemd/install.sh --uninstall  # stop and remove
systemctl --user status local-connect-status.timer local-connect-status-web.service
journalctl --user -u local-connect-status.service -n 50
```

GitHub is read with the `gh` CLI using its stored login; no token is written anywhere and
the dashboard is static HTML with the data embedded, so the browser never holds a credential.

**Missed events** recover automatically: the collector compares each repository's current
head to the last head whose full change range it read, so a merge that landed while the machine
was off is seen on the next tick, with every intermediate commit listed in the change record.
A transient diff or commit-log failure keeps that comparison baseline in place for the next tick
without hiding the newly confirmed current head.

**Heavy checks** are opt-in. They include Document Summarizer's Rust suite and the exact-tree
Email Watcher → Document Summarizer PDF handoff. The handoff first validates the current Connect
Contracts fixtures, builds the exact provider revision with that revision's test keyring, and then
runs Email Watcher's real cross-process proof under software rendering with its fixture model. Its
single evidence row records all three revisions, so any participant move makes the proof historical.
Routine Document Summarizer evidence remains its GitHub Actions run for the exact sha.

## What it cannot do

- Run a human demonstration. The Invoice Processor removal test needs `sudo dpkg`; the
  missing Linux/Windows installer checks need their target operating system; the calendar
  write needs a real Microsoft tenant. A person runs those and records the result
  with `scripts/record_observation.py`, which requires `--observed-by` and an artifact.
- Touch a GPU. The cross-app acceptance runs with the stand-in model only.
- Mint a Connect entitlement. The products accept only a licence signed by their production
  authority, so the cross-app acceptance stages the operator's installed licence
  (`~/.config/local-connect/entitlement-v1.json`, resolved exactly as the products resolve it) into
  its isolated installation for the run and removes it afterwards. Without a present, in-window
  licence the check records **unavailable** and the run is red; it never records a failure it did
  not observe.
- Treat an issue count as completion evidence. Issues explain why a release is blocked; checks,
  installed demonstrations, and published artifacts prove release state.
- Require Automate for the first public release. Automate remains tracked, but its unattended
  rules and scheduled workflows are explicitly deferred to later releases.

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
| let evidence from a replaced/reconfigured check or a changed condition claim satisfy a condition, or treat a new fingerprint-free row as migrated legacy evidence | `test_condition_evidence_must_come_from_its_current_configured_check`, `test_condition_evidence_must_match_the_current_check_configuration`, `test_condition_evidence_must_match_the_current_claim_semantics`, `test_authenticated_legacy_prefix_preserves_only_unchanged_check_evidence` |
| promote anything on a source-string match, or treat a missing string as proof of absence | `test_source_inspection_is_inconclusive_and_never_raises_maturity`, `test_code_change_maps_to_task_and_marker_rename_alone_cannot_prove_removal` |
| let Linux evidence stand in for Windows, ignore a check's configured platform, or call an app or bundle ready without its installed platform observations | `test_linux_only_evidence_leaves_windows_not_checked_and_blocks_release_readiness`, `test_check_platform_filters_mixed_evidence_when_condition_omits_platform`, `test_each_release_gate_requires_platform_installed_observations` |
| count an issue as capability proof, ignore a first-release blocker, lose the gate during render-only, or treat an unavailable issue source as empty | `test_open_release_issue_blocks_readiness_but_never_erases_capability_evidence`, `test_unavailable_or_missing_issue_evidence_blocks_readiness_instead_of_looking_empty`, `test_render_only_reconstructs_latest_issue_gate_from_records`, `test_github_issue_runner_records_only_the_exact_milestone_and_fails_loud` |
| show pending / unavailable / partial as green | `test_non_results_are_not_checked` |
| call something released without uploaded, nonempty installers whose manifest hashes match GitHub's asset digests, a published release, local licence proof, or first-run model guidance | `test_release_verdict_requires_a_published_release_with_every_required_asset`, `test_release_checksum_download_failure_is_unavailable`, `test_release_asset_download_rejects_binary_checksum_content`, `test_release_requires_release_artifact_not_just_demos`, `test_release_promise_requires_licence_and_first_run_model_evidence`, `test_release_absence_is_an_explicit_not_met_at_current_head` |
| duplicate progress on a repeated delivery, lose a recovery across volatile source metadata, changed incomplete release, or distinct change range | `test_duplicate_delivery_stores_once`, `test_pass_fail_pass_recovery_is_retained_and_wins_after_reload`, `test_actions_recovery_survives_volatile_source_metadata`, `test_changed_incomplete_release_is_not_deduplicated`, `test_change_records_from_different_baselines_are_both_kept`, `test_recent_changes_supersede_retry_without_collapsing_distinct_baselines` |
| let a docs-only or contract-only change look like running functionality | `test_docs_only_change_is_flagged_and_unmapped_files_surface` |
| show a failed fetch, mirror command exception, unreadable commit range, missing repository, or unavailable/malformed GitHub head as a clean empty result, including when fetch was intentionally skipped; advance past a failed change range before retrying it; or leave a retry warning after that range succeeds | `test_missing_repository_and_unavailable_github_are_explicit`, `test_mirror_subprocess_errors_become_failures`, `test_empty_commit_range_and_failed_commit_read_stay_distinct`, `test_failed_change_read_retries_before_advancing_baseline`, `test_no_fetch_and_unavailable_github_leave_cached_mirror_unknown`, `test_default_branch_head_requires_a_full_commit_sha` |
| persist a baseline for an unknown repository, malformed or unresolved SHA, duplicate assignment, or partially valid batch | `test_set_baseline_seeds_display_head_and_change_baseline`, `test_set_baseline_rejects_invalid_batch_without_changing_state` |
| invent data when only rendering, re-promote evidence after a head became unknown, or hide a confirmed head for an unrelated legacy failure | `test_render_only_never_invents_data`, `test_render_only_excludes_head_marked_unknown_and_does_not_repromote_old_pass`, `test_legacy_failed_head_is_excluded_from_render_heads`, `test_legacy_non_head_failure_keeps_confirmed_render_head` |
| label a task with nothing checkable as "verified at current code" | `test_task_with_only_inspection_conditions_is_not_checked_not_current` |
| let another repository's release satisfy an app condition, or dispatch an app release check across every repository | `test_foreign_repository_release_record_cannot_satisfy_app_condition`, `test_release_dispatch_is_scoped_to_the_configured_repository` |
| prefer an older workflow rerun over a newer workflow run | `test_latest_distinct_workflow_run_beats_older_rerun_attempt` |
| overwrite or lose a stored record's log, keep a duplicate log per tick, name a file an execution did not write, or touch legacy logs | `test_identical_executions_keep_one_log_set`, `test_changed_result_keeps_both_log_sets`, `test_collapsed_execution_removal_failure_is_only_a_warning`, `test_timeout_and_startup_failure_name_only_what_was_written`, `test_invoice_handoff_timeout_writes_nothing_and_names_no_log`, `test_pdf_handoff_timeout_keeps_gathered_output_under_the_execution_name`, `test_legacy_fixed_name_files_are_never_touched`, `test_execution_names_carry_the_start_instant_and_never_collide`, `test_render_only_writes_and_removes_nothing` |
| delete an unowned compatibility path, read cross-app fixtures from developer checkouts, or claim the PDF handoff without the exact consumer/provider/contracts tuple | `test_prepare_owned_symlink_refuses_real_directory_without_deleting_it`, `test_prepare_owned_symlink_refuses_foreign_symlink`, `test_cross_app_runner_uses_all_exact_trees_and_isolated_home`, `test_pdf_handoff_runner_binds_real_proof_to_all_exact_trees`, `test_pdf_handoff_runner_records_nonzero_proof_as_failure` |
| record a harness-caused entitlement failure as product evidence, leak the operator's configuration into the isolated installation, or leave the staged licence behind | `test_cross_app_runner_records_unavailable_without_installed_entitlement`, `test_cross_app_runner_records_unavailable_for_expired_or_not_yet_valid_entitlement`, `test_cross_app_runner_records_unavailable_for_malformed_entitlement`, `test_cross_app_runner_inherited_xdg_config_home_does_not_leak`, `test_cross_app_runner_relative_xdg_config_home_is_unreadable`, `test_cross_app_runner_records_unavailable_without_configuration_root`, `test_cross_app_runner_removes_staged_entitlement_on_every_exit`, `test_cross_app_runner_removes_partially_staged_entitlement_when_staging_fails`, `test_cross_app_runner_records_watcher_decision_line` |
| let inherited `PYTHONPATH` or `PYTHONHOME` redirect an exact-tree pytest run into a developer checkout | `test_pytest_clears_inherited_python_paths_and_records_startup_error` |
| hide unavailable Actions, Releases, source-inspection, or local-runner sources, trust a partial desktop dependency directory, crash during dependency setup/runtime startup, or fail the web service before generated `site/` exists | `test_unavailable_results_from_any_runner_are_run_failures`, `test_unavailable_local_runner_path_sets_failed_exit_and_banner`, `test_uv_sync_timeout_becomes_an_explicit_failure`, `test_editable_install_timeout_becomes_an_explicit_failure`, `test_desktop_dependency_setup_errors_become_explicit_failures`, `test_desktop_dependency_cache_requires_success_marker_for_current_lockfile`, `test_pytest_clears_inherited_python_paths_and_records_startup_error`, `test_cargo_startup_error_becomes_unavailable_evidence`, `test_cross_app_startup_error_becomes_unavailable_evidence`, `test_web_service_creates_generated_site_before_serving_it` |
| publish stale status copy, call an incomplete release absent, or embed a record that terminates the dashboard script | `test_next_action_is_derived_from_condition_state_not_catalogue_copy`, `test_release_failure_label_distinguishes_incomplete_from_absent_release`, `test_dashboard_json_cannot_terminate_its_script_and_footer_is_evidence_driven` |
| accept forged manual participant metadata or interpolate evidence fields as executable dashboard markup | `test_participant_parser_rejects_ambiguous_or_constructed_metadata`, `test_participant_parser_accepts_exact_catalogue_set_at_sha_boundary`, `test_manual_record_rejects_participant_revision_absent_from_owned_mirror`, `test_dashboard_escapes_every_manual_revision_field_before_inner_html` |
| keep a release green after its assets disappear because publication time outranks the later check | `test_release_record_uses_target_commit_time_not_publication_time`, `test_current_release_failure_beats_old_pass_with_inflated_publication_time` |
| let a backfilled, future-dated, or pre-commit manual pass displace a newer observed failure or claim revisions that did not exist yet | `test_manual_record_uses_normalized_observation_time`, `test_manual_observation_time_allows_clock_skew_but_rejects_material_future`, `test_manual_observation_must_follow_every_participant_revision_with_clock_skew`, `test_manual_record_rejects_observation_before_any_participant_revision`, `test_backfilled_older_manual_pass_cannot_displace_newer_observed_failure` |
| promise that failed-source rows always show historical proof when they may show the failed attempt | `test_source_failure_banner_matches_current_or_historical_row_evidence` |
| order evidence, stored heads, or recent changes lexicographically instead of by absolute instant | `test_last_proven_uses_absolute_instant_across_offsets`, `test_store_latest_revision_uses_absolute_instant_across_offsets`, `test_recent_changes_are_ordered_by_absolute_instant_across_offsets` |

Two runs cannot interleave: the collector takes an exclusive lock on `data/.lock`, and a
baseline write waits for a running collection to finish rather than racing it.
The systemd unit does not impose a shorter outer start deadline: runner subprocesses keep their
own bounded timeouts so the collector can store unavailable evidence and render before exiting.

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
