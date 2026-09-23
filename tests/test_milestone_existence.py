"""Contract 05: a gate that cannot find its milestone is unavailable, not clear."""

from __future__ import annotations

from pathlib import Path

from lcstatus.sources import Failure, Revision
from lcstatus.verify import Runner

HEAD = "c" * 40
TITLE = "First Public Release"
CHECK = {"runner": "github_issues", "repo": "app", "platform": "n/a", "milestone": TITLE}
CATALOGUE = {"repos": {"app": {"github": "owner/app"}}, "tasks": []}


def issue(number, *, title="Issue", milestone=TITLE, pull=False, labels=("bug",)):
    item = {"number": number, "title": title, "milestone": {"title": milestone} if milestone else None,
            "labels": [{"name": name} for name in labels]}
    if pull:
        item["pull_request"] = {"url": f"https://api.github.com/repos/owner/app/pulls/{number}"}
    return item


def milestone(*, title=TITLE, number=1, state="open", open_issues=0):
    return {"title": title, "number": number, "state": state, "open_issues": open_issues}


class FakeGitHub:
    def __init__(self, milestones, items):
        self._milestones, self._items = milestones, items
        self.calls: list[str] = []

    def milestones(self, repo):
        self.calls.append("milestones")
        return self._milestones

    def open_issues_and_pulls(self, repo):
        self.calls.append("issues")
        return self._items


def run(tmp_path: Path, gh):
    runner = Runner(object(), tmp_path / "cache", tmp_path / "logs", gh, CATALOGUE)
    return runner.release_issues("issues.app", CHECK, Revision("app", HEAD, "2026-09-23T00:00:00Z", "head"), ["g"], ["t"])


def test_missing_milestone_is_unavailable_not_clear(tmp_path: Path):
    gh = FakeGitHub([milestone(title="Later", number=7)], [])
    record = run(tmp_path, gh)
    assert record.verdict == "unavailable"
    assert record.summary == f"milestone not found: {TITLE}"
    assert record.detail == {"milestone": TITLE, "milestones_seen": ["Later"]}
    assert gh.calls == ["milestones"]                      # the issues were never asked for
    # an empty repository is the same: nothing found is nothing confirmed
    empty = run(tmp_path, FakeGitHub([], []))
    assert empty.verdict == "unavailable" and empty.detail["milestones_seen"] == []


def test_milestone_listing_failure_or_malformed_response_is_unavailable(tmp_path: Path):
    for gh, summary in ((FakeGitHub(Failure("gh", "timed out"), []), "gh: timed out"),
                        (FakeGitHub({"not": "a list"}, []), "GitHub milestones response was not a list")):
        record = run(tmp_path, gh)
        assert record.verdict == "unavailable" and record.summary == summary
        assert gh.calls == ["milestones"]                  # the issues were never asked for
    good = milestone()
    for bad in (
        "text",
        {**good, "title": 5},
        {k: v for k, v in good.items() if k != "number"},
        {**good, "number": True},
        {**good, "state": "archived"},
        {**good, "open_issues": "0"},
        {**good, "open_issues": False},
    ):
        record = run(tmp_path, FakeGitHub([bad], []))
        assert record.verdict == "unavailable", bad
        assert record.summary == "GitHub milestones response contained a malformed milestone"
    twice = run(tmp_path, FakeGitHub([milestone(number=1), milestone(number=2)], []))
    assert twice.verdict == "unavailable" and twice.summary == f"GitHub returned 2 milestones titled {TITLE}"


def test_found_milestone_with_no_open_issues_is_clear_and_recorded(tmp_path: Path):
    record = run(tmp_path, FakeGitHub([milestone(number=3)], [issue(9, milestone="Later")]))
    assert record.verdict == "pass"
    assert record.summary == f"0 open issues in {TITLE}"
    assert record.detail == {"milestone": TITLE, "milestone_number": 3, "milestone_state": "open", "issues": []}


def test_open_issues_block_and_pull_requests_do_not(tmp_path: Path):
    items = [issue(2, title="Second"), issue(1, title="First"), issue(3, title="A fix", pull=True)]
    record = run(tmp_path, FakeGitHub([milestone(open_issues=3)], items))
    assert record.verdict == "fail"
    assert record.summary == f"2 open issues in {TITLE}"
    assert [b["number"] for b in record.detail["issues"]] == [1, 2]
    assert "count_disagreement" not in record.detail
    # a pull request alone: the count agrees, nothing blocks
    only_pull = run(tmp_path, FakeGitHub([milestone(open_issues=1)], [issue(3, pull=True)]))
    assert only_pull.verdict == "pass" and only_pull.detail["issues"] == []


def test_count_disagreement_is_recorded_never_decisive(tmp_path: Path):
    # GitHub's counter can stay stale; the listing decides, the disagreement is shown
    stale_high = run(tmp_path, FakeGitHub([milestone(open_issues=2)], [issue(1)]))
    assert stale_high.verdict == "fail" and [b["number"] for b in stale_high.detail["issues"]] == [1]
    assert stale_high.detail["count_disagreement"] == {"listed": 1, "reported": 2}
    stale_low = run(tmp_path, FakeGitHub([milestone(open_issues=0)], [issue(1)]))
    assert stale_low.verdict == "fail" and stale_low.detail["count_disagreement"] == {"listed": 1, "reported": 0}
    stale_clear = run(tmp_path, FakeGitHub([milestone(open_issues=3)], []))
    assert stale_clear.verdict == "pass" and stale_clear.detail["count_disagreement"] == {"listed": 0, "reported": 3}
    agreed = run(tmp_path, FakeGitHub([milestone(open_issues=2)], [issue(1), issue(2)]))
    assert agreed.verdict == "fail" and "count_disagreement" not in agreed.detail


def test_closed_milestone_is_still_found(tmp_path: Path):
    record = run(tmp_path, FakeGitHub([milestone(state="closed")], []))
    assert record.verdict == "pass" and record.detail["milestone_state"] == "closed"
    still_open = run(tmp_path, FakeGitHub([milestone(state="closed", open_issues=1)], [issue(4)]))
    assert still_open.verdict == "fail" and still_open.detail["milestone_state"] == "closed"


def test_issue_listing_failure_after_a_found_milestone_is_unavailable(tmp_path: Path):
    record = run(tmp_path, FakeGitHub([milestone()], Failure("gh", "rate limited")))
    assert record.verdict == "unavailable" and record.summary == "gh: rate limited"
    assert record.detail["milestone_number"] == 1       # what was confirmed stays recorded
    assert run(tmp_path, FakeGitHub([milestone()], "text")).summary == "GitHub issues response was not a list"
