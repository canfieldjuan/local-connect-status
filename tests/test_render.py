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
    assert "Release status comes from the release-evidence rows above." in dashboard



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
