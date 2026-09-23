# Contract 05 — A gate that cannot find its milestone is unavailable, not clear

Status: **accepted 2026-09-23** (operator: "go"; rev 2 after independent review of the implementation withdraws
B3's verdict effect: GitHub's milestone counter is recorded, never decisive — see D3). Slice 5 of the
2026-09-18 fix plan.
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

B2. **The found milestone is recorded.** `detail` carries `milestone_number` and `milestone_state` from
the milestone object, beside today's `milestone` and `issues`. A **closed** milestone is still a found
milestone: its state is recorded and the verdict still comes from the open issues listed under it
(closing a milestone does not close its issues). The milestone's `open_issues` counter is not recorded
as such: it is GitHub's denormalised value and would churn the row's identity every time a pull request
enters or leaves the milestone.

B3. **A disagreeing counter is recorded, never decisive.** The runner counts every open item (issues
**and** pull requests, i.e. the raw `/issues` listing before the pull-request filter) whose milestone
title matches, and compares that count with the milestone object's `open_issues`. When they differ,
`detail.count_disagreement = {"listed": <n>, "reported": <m>}` is recorded and the verdict is still taken
from the listing. The listing is the authoritative source; the counter is a hint (D3). Blockers remain
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
I3. The gate can read clear only after (a) the milestone was seen and (b) the listing succeeded. Every
other outcome is `unavailable`. A counter disagreement is visible in `detail`, never a verdict.
I4. An unexpected response shape is `unavailable`, never `pass` and never `fail`: a `fail` would name
blockers that may not exist; a `pass` would clear a gate nothing confirmed.

## Failure cases

| Situation | Result |
|---|---|
| milestone absent from the repository | `unavailable`, "milestone not found: First Public Release", titles seen recorded |
| milestone listing API fails | `unavailable`, "<what>: <why>" |
| milestone listing malformed (not a list; item without title/number/state/open_issues) | `unavailable` |
| milestone found, listing fails | `unavailable`, as today |
| milestone found, 0 open issues | `pass`, "0 open issues in <title>" (as today) with number and state recorded |
| milestone found, N open issues | `fail`, blockers listed (as today) |
| milestone found, closed, 0 open | `pass`, `milestone_state: closed` |
| a pull request in the milestone, no issues | `pass`; the pull request is not a blocker |
| listing shows 1 issue, milestone reports 2 (stale counter) | verdict from the listing (`fail`, 1 blocker); `count_disagreement: {listed: 1, reported: 2}` recorded |
| milestone renamed on GitHub | `unavailable` until the catalogue's `milestone` and the release's `issue_gate.milestone` are updated together (`catalogue.load` already requires them to match) |

## Concurrency model

Two GitHub reads per repository per tick instead of one; both read-only. A change on GitHub between the
two reads can show as a recorded count disagreement for that tick; the verdict always follows the listing.

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
5. `test_count_disagreement_is_recorded_never_decisive`: 1 listed vs 2 reported → `fail` with one blocker
   and `count_disagreement` recorded; 2 vs 2 → no such key.
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
D3. **The counter never decides (rev 2).** Rev 1 made a listing/counter disagreement `unavailable`, on
the premise that the counter tracks the listing and a disagreement is a transient race. Independent
review measured otherwise on real data: on a large public repository the milestone reported 110 open
while the listing held 106 (102 issues, 4 pull requests), stable across runs and confirmed by the search
API — GitHub's counter is denormalised and can stay wrong indefinitely. Under rev 1 such a repository
would read `unavailable` on every tick with a next action ("restore visibility") that is false. The
review also established that `gh api --paginate` exits non-zero on any non-2xx page, so a short listing
without a `Failure` is not a path this client has; the cross-check guarded nothing real. The listing is
the authority; the counter is recorded when it disagrees so an operator can see it, and it still counts
pull requests, because GitHub's does.
D4. **Every "cannot confirm" outcome is `unavailable`, never `fail`.** A `fail` would show blockers the
evidence does not contain; `unavailable` fails readiness closed and names the reason.

## Estimated diff

~15 lines in `sources.py`, ~45 in `verify.py`, ~160 lines of tests, 2 README lines.
