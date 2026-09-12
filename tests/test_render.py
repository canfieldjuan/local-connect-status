"""Rendered status copy and embedding must stay tied to evidence."""

from __future__ import annotations

from lcstatus.evidence import Record
from lcstatus.render import dashboard_html, report_md, status_payload
from lcstatus.rules import ConditionStatus, TaskStatus


def payload_with_summary(summary: str) -> dict:
    record = Record(
        kind="source_inspection", repo="ew", revision="a" * 40,
        verdict="inconclusive", condition_ids=["condition"], summary=summary,
        recorded_at="2026-09-11T00:00:00+00:00",
    )
    condition = ConditionStatus(
        {"id": "condition", "kind": "source_inspection", "check": "inspect",
         "proves": "queue | scheduler"},
        "inconclusive",
        current=record,
    )
    task = TaskStatus(
        {
            "id": "task", "app": "email-watcher", "app_repo": "ew",
            "layer": "automate", "title": "Automation", "promise": "Starts from a rule.",
            "human_involvement": "Review the result.", "next_action": "stale catalogue copy",
            "depends_on": [],
        },
        "planned",
        "no_evidence",
        [condition],
        {},
        [],
    )
    catalogue = {
        "release": {"target": "local installers", "automate_scope": {"decision": "undecided", "note": "operator choice"}},
    }
    return status_payload(
        catalogue, [task], {}, [], {"runs": 1, "last_run_at": "2026-09-11T00:00:00+00:00"}, {}, [record]
    )


def test_next_action_is_derived_from_condition_state_not_catalogue_copy():
    payload = payload_with_summary("inspection only")
    action = payload["tasks"][0]["next_action"]
    assert action.startswith("Add behavioral proof beyond source inspection (inspect):")
    assert "stale catalogue copy" not in action


def test_markdown_evidence_escapes_tables_and_preserves_source_summary():
    payload = payload_with_summary("found | marker\nsecond line")
    report = report_md(payload, {"release": payload["release"]})
    assert r"queue \| scheduler" in report
    assert r"found \| marker<br>second line" in report


def test_dashboard_json_cannot_terminate_its_script_and_footer_is_evidence_driven():
    payload = payload_with_summary("</script><script>alert(1)</script>")
    dashboard = dashboard_html(payload, {"release": payload["release"]})
    assert "</script><script>alert(1)</script>" not in dashboard
    assert "\\u003c/script\\u003e" in dashboard
    assert "No app has a downloadable release" not in dashboard
    assert "Release status comes from both the release-evidence rows and the issue gate above." in dashboard


def test_dashboard_escapes_every_manual_revision_field_before_inner_html():
    payload = payload_with_summary("manual observation")
    evidence = payload["tasks"][0]["conditions"][0]["current"]
    evidence["revision"] = '<img src=x onerror="alert(1)">'
    evidence["participants"] = {
        '<img src=x onerror="name()">': '<img src=x onerror="sha()">',
    }

    dashboard = dashboard_html(payload, {"release": payload["release"]})

    assert "<img src=x onerror=" not in dashboard
    assert "${esc(e.revision)}" in dashboard
    assert "`${esc(r)}@${esc(v)}`" in dashboard
    assert "${esc(c.last_proven.revision)}" in dashboard



def test_source_failure_banner_matches_current_or_historical_row_evidence():
    payload = payload_with_summary("Actions unavailable")
    payload["source_failures"] = [
        {"repo": "ew", "what": "github_actions", "why": "timed out"}
    ]
    report = report_md(payload, {"release": payload["release"]})
    dashboard = dashboard_html(payload, {"release": payload["release"]})
    accurate = (
        "Affected rows are not treated as freshly verified; each row shows the applicable "
        "current attempt or last proven result."
    )
    assert accurate in report
    assert accurate in dashboard
    assert "show the last proven result, not a fresh one" not in report
    assert "show the last proven result, not a fresh one" not in dashboard


def release_failure_payload(detail: dict) -> dict:
    record = Record(
        kind="release_artifact", repo="ip", revision="b" * 40,
        verdict="fail", condition_ids=["release"], detail=detail,
        recorded_at="2026-09-11T00:00:00+00:00",
    )
    condition = ConditionStatus(
        {"id": "release", "kind": "release_artifact", "check": "release.check",
         "proves": "published installers"},
        "check_failed",
        current=record,
    )
    task = TaskStatus(
        {
            "id": "release-task", "app": "invoice-processor", "app_repo": "ip",
            "layer": "release", "title": "Release", "promise": "Downloadable installers.",
            "depends_on": [],
        },
        "planned", "not_checked", [condition], {}, [],
    )
    catalogue = {
        "release": {
            "target": "local installers",
            "automate_scope": {"decision": "undecided", "note": "operator choice"},
        }
    }
    return status_payload(
        catalogue, [task], {}, [], {"runs": 1, "last_run_at": "2026-09-11T00:00:00+00:00"}, {}, [record]
    )


def test_release_failure_label_distinguishes_incomplete_from_absent_release():
    incomplete = release_failure_payload({"missing": ["windows installer"]})
    absent = release_failure_payload({"count": 0})

    assert incomplete["tasks"][0]["conditions"][0]["label"] == "release incomplete"
    assert absent["tasks"][0]["conditions"][0]["label"] == "no release published"
    report = report_md(incomplete, {"release": incomplete["release"]})
    dashboard = dashboard_html(incomplete, {"release": incomplete["release"]})
    assert "release incomplete" in report and "no release published" not in report
    assert '"label": "release incomplete"' in dashboard


def test_recent_changes_are_ordered_by_absolute_instant_across_offsets():
    lexically_later_but_older = Record(
        kind="change", repo="ip", revision="a" * 40, verdict="pass",
        revision_time="2026-09-11T10:00:00+02:00", recorded_at="2026-09-11T10:01:00+02:00",
        summary="old -> aaaaaaaaaaaa: 1 files", detail={"old": "old"},
    )
    lexically_earlier_but_newer = Record(
        kind="change", repo="ew", revision="b" * 40, verdict="pass",
        revision_time="2026-09-11T09:30:00+00:00", recorded_at="2026-09-11T09:31:00+00:00",
        summary="old -> bbbbbbbbbbbb: 1 files", detail={"old": "old"},
    )
    payload = status_payload(
        {"release": {}}, [], {}, [], {"runs": 1, "last_run_at": "2026-09-11T10:00:00+00:00"}, {},
        [lexically_later_but_older, lexically_earlier_but_newer],
    )
    assert [item["repo"] for item in payload["recent_changes"]] == ["ew", "ip"]


def test_recent_changes_supersede_retry_without_collapsing_distinct_baselines():
    common = {
        "kind": "change", "repo": "ew", "revision": "c" * 40, "verdict": "pass",
        "revision_time": "2026-09-11T10:00:00+00:00", "recorded_at": "2026-09-11T10:01:00+00:00",
    }
    incomplete = Record(
        **common, summary="aaaaaaaaaaaa -> cccccccccccc: 1 files",
        detail={"old": "a" * 40, "commits": [], "commits_complete": False},
    )
    complete = Record(
        **common, summary="aaaaaaaaaaaa -> cccccccccccc: 1 files",
        detail={"old": "a" * 40, "commits": ["retry succeeded"], "commits_complete": True},
    )
    distinct = Record(
        **common, summary="bbbbbbbbbbbb -> cccccccccccc: 2 files",
        detail={"old": "b" * 40, "commits": ["distinct range"], "commits_complete": True},
    )

    payload = status_payload(
        {"release": {}}, [], {}, [], {"runs": 1, "last_run_at": common["recorded_at"]}, {},
        [incomplete, distinct, complete],
    )

    by_start = {item["from"]: item for item in payload["recent_changes"]}
    assert set(by_start) == {"a" * 40, "b" * 40}
    assert by_start["a" * 40]["commits"] == ["retry succeeded"]
    assert by_start["a" * 40]["commits_complete"] is True
