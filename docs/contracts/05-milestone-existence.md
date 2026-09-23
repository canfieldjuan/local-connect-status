# Contract 05 — A gate that cannot find its milestone is unavailable, not clear

Status: **proposed** (review before any code). Slice 5 of the 2026-09-18 fix plan.
Scope: `lcstatus/sources.py` (one milestone-listing helper, one raw open-items helper), `lcstatus/verify.py`
(`Runner.release_issues`), tests, one README sentence. No rules change, no render change, no catalogue edit,
no product repository.

## Problem (evidence, not description)

- `Runner.release_issues` (`lcstatus/verify.py:974-1046`) lists a repository's open issues
  (`sources.py:249-256`, pull requests excluded) and keeps those whose `milestone.title` equals the
  configured title. It never asks whether that milestone exists. A repository where the milestone was
  never created, was renamed, or was deleted therefore yields zero matching issues, verdict `pass`,
  summary "0 open issues in First Public Release" — and the gate reads **clear**: `task_status`
  (`rules.py`) treats a passing gate as satisfied and readiness can pass on it. The catalogue's own
  note for every gate check says "API failure is unavailable, never an empty gate"; the code honours
  that for API failure only.
- Nothing cross-checks the listing against the milestone's own `open_issues` counter. A listing that
  comes back short without a `Failure` (a pagination fault, a race) under-reports blockers silently.
- Live (2026-09-23): all four repositories carry an open "First Public Release" milestone with
  `open_issues: 0`, and the four live gate rows read `pass`, "0 open issues". Today's clear is truthful,
  so nothing changes at merge; the defect is what a missing or renamed milestone would do.

## Observable behaviour

B1. **Existence before listing.** `release_issues` first lists the repository's milestones
(`repos/{gh}/milestones?state=all&per_page=100`, paginated to completion). Exactly one milestone whose
`title` equals the configured title must exist. When none does, the record is `unavailable` with summary
`milestone not found: <title>` and `detail.milestones_seen` = the titles that do exist (sorted). When the
milestone listing fails, or is not a list, or contains an item without a string `title`, an integer
`number`, a string `state` in {open, closed} and an integer `open_issues`, the record is `unavailable`
with a summary naming the failure. Two milestones with the same title cannot exist in one repository;
should the API ever return two, the record is `unavailable`.

B2. **The found milestone is recorded.** `detail` carries `milestone_number`, `milestone_state` and
`milestone_open_issues` from the milestone object, beside today's `milestone` and `issues`. A **closed**
milestone is still a found milestone: its state is recorded and the verdict still comes from the open
issues listed under it (closing a milestone does not close its issues).

B3. **The listing must agree with the milestone.** The runner counts every open item (issues **and** pull
requests, i.e. the raw `/issues` listing before the pull-request filter) whose milestone title matches,
and compares that count with the milestone object's `open_issues` (which GitHub maintains over issues and
pull requests alike). When they differ, the record is `unavailable` with summary
`issue listing disagrees with milestone count (<listed> listed, <reported> reported)`. Blockers remain
the open **issues** only, as today: a pull request in the milestone is not a blocker and is not listed.

B4. **What the rules do with it is unchanged.** An `unavailable` gate row reads `not_checked`, the task's
issue gate reads `unavailable`, readiness fails closed, and the next action is "Restore GitHub issue
visibility for: <repo>" (contract 04 rev 4 B5). `pass` and `fail` rows behave exactly as today.

B5. **README** gains one sentence under the issue gate: the gate reads clear only after the milestone was
found in the repository and the listing agreed with its count; a missing milestone is unavailable, never
clear.

## Invariants

I1. Evidence is never rewritten; the store stays append-only.
I2. **No label changes at merge.** Verified live before merge: the branch's runner, run read-only against
the four live repositories, returns four `pass` rows whose verdict and summary equal the live rows, with
the new detail fields populated and the counts in agreement; the derived page is identical to the served
one.
I3. The gate can read clear only after (a) the milestone was seen, (b) the listing succeeded, and (c) the
listing's count agreed with the milestone's. Every other outcome is `unavailable`.
I4. An unexpected response shape is `unavailable`, never `pass` and never `fail`: a `fail` would name
blockers that may not exist; a `pass` would clear a gate nothing confirmed.

## Failure cases

| Situation | Result |
|---|---|
| milestone absent from the repository | `unavailable`, "milestone not found: First Public Release", titles seen recorded |
| milestone listing API fails | `unavailable`, "<what>: <why>" |
| milestone listing malformed (not a list; item without title/number/state/open_issues) | `unavailable` |
| milestone found, listing fails | `unavailable`, as today |
| milestone found, 0 open issues, count 0 | `pass`, "0 open issues in <title>" (as today) with the new detail fields |
| milestone found, N open issues, count N | `fail`, blockers listed (as today) |
| milestone found, closed, 0 open | `pass`, `milestone_state: closed` |
| a pull request in the milestone, no issues | `pass`, count 1 == 1; the pull request is not a blocker |
| listing shows 1 issue, milestone reports 2 (short listing or race) | `unavailable`, "issue listing disagrees with milestone count (1 listed, 2 reported)"; the next tick re-reads |
| milestone renamed on GitHub | `unavailable` until the catalogue's `milestone` and the release's `issue_gate.milestone` are updated together (`catalogue.load` already requires them to match) |

## Concurrency model

Two GitHub reads per repository per tick instead of one; both read-only. A change on GitHub between the
two reads shows as a count disagreement for that tick (`unavailable`), never as a wrong verdict.

## Settling test evidence

Unit (runner with a fake GitHub client, as the existing gate tests do):
1. `test_missing_milestone_is_unavailable_not_clear`: no milestone with the title → `unavailable`, summary
   and `milestones_seen`; and the issues listing is never requested.
2. `test_milestone_listing_failure_or_malformed_response_is_unavailable`: `Failure`, a non-list, an item
   missing each required field, a duplicate title.
3. `test_found_milestone_with_no_open_issues_is_clear_and_recorded`: `pass`, summary unchanged, new
   detail fields present.
4. `test_open_issues_block_and_pull_requests_do_not`: two issues and one pull request in the milestone,
   `open_issues: 3` → `fail` with the two issues as blockers.
5. `test_listing_count_must_match_the_milestone_count`: 1 listed vs 2 reported → `unavailable` with the
   exact summary; 2 vs 2 → verdict from the issues.
6. `test_closed_milestone_is_still_found`: closed, 0 open → `pass`, `milestone_state: closed`.
7. The existing gate tests stay green with the fake extended to serve a milestone list.

Live (read-only, before merge): the branch's runner against the four live repositories through the real
client, comparing verdict, summary and blockers with the live rows (equal) and printing the new detail
fields; the derived page identical to the served one (I2).

## Decisions

D1. **Existence comes from the milestones endpoint, not from "at least one issue".** An empty milestone
is a legitimate clear; an absent one is not. Only the milestones listing distinguishes them.
D2. **A closed milestone still counts as found.** Closing is an operator act that says the milestone is
done; the gate's job is the open issues, which closing does not touch. The state is recorded so the
page can say so.
D3. **The count cross-check includes pull requests** because GitHub's `open_issues` does; comparing the
filtered listing with it would disagree whenever a pull request sits in the milestone.
D4. **Every "cannot confirm" outcome is `unavailable`, never `fail`.** A `fail` would show blockers the
evidence does not contain; `unavailable` fails readiness closed and names the reason.

## Estimated diff

~15 lines in `sources.py`, ~45 in `verify.py`, ~160 lines of tests, 2 README lines.
