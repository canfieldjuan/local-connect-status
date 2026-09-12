"""The first-release contract keeps evidence, issue blockers, and Automate scope distinct."""

from __future__ import annotations

from pathlib import Path

from lcstatus.catalogue import load
from lcstatus.collect import collect_release_issues, release_issue_state
from lcstatus.evidence import Record, check_fingerprint, condition_fingerprint
from lcstatus.render import dashboard_html, report_md, status_payload
from lcstatus.rules import task_status
from lcstatus.sources import Failure


ROOT = Path(__file__).resolve().parent.parent
HEAD = "a" * 40


def _release_fixture() -> tuple[dict, dict, list[Record]]:
    catalogue = {
        "release": {"required_platforms": ["linux", "windows"]},
        "checks": {
            "linux": {"runner": "ci_job", "repo": "app", "platform": "linux"},
            "windows": {"runner": "manual_observation", "repo": "app", "platform": "windows"},
        },
    }
    task = {
        "id": "release.app",
        "app": "app",
        "app_repo": "app",
        "layer": "release",
        "release_issue_repos": ["app"],
        "conditions": [
            {"id": "linux", "kind": "ci_run", "check": "linux", "platform": "linux"},
            {"id": "windows", "kind": "installed_demo", "check": "windows", "platform": "windows"},
        ],
    }
    records = []
    for condition in task["conditions"]:
        check = catalogue["checks"][condition["check"]]
        records.append(Record(
            kind=condition["kind"], repo="app", revision=HEAD, verdict="pass",
            platform=condition["platform"], condition_ids=[condition["id"]],
            source={
                "check": condition["check"],
                "check_fingerprint": check_fingerprint(check),
                "condition_fingerprints": {
                    condition["id"]: condition_fingerprint(condition),
                },
            },
            executed=1 if condition["kind"] == "ci_run" else None,
            failed=0 if condition["kind"] == "ci_run" else None,
        ))
    return catalogue, task, records


def test_accepted_contract_splits_app_and_bundle_release_and_defers_automate():
    catalogue = load(ROOT / "catalogue.json")

    assert catalogue["release"]["automate_scope"]["required_for_first_release"] is False
    assert catalogue["release"]["issue_gate"]["milestone"] == "First Public Release"
    release_tasks = {task["id"]: task for task in catalogue["tasks"] if task["layer"] == "release"}
    assert set(release_tasks) == {
        "release.email_watcher",
        "release.document_summarizer",
        "release.invoice_processor",
        "release.local_connect_bundle",
    }
    email_conditions = {condition["id"] for condition in release_tasks["release.email_watcher"]["conditions"]}
    assert "rel.ew_windows_installer_demo" in email_conditions
    assert release_tasks["release.local_connect_bundle"]["release_issue_repos"] == [
        "eom-email-watcher",
        "document-summarizer",
        "invoice-processor",
        "connect-contracts",
    ]
    entitlement_checks = {
        condition["check"]
        for condition in release_tasks["release.local_connect_bundle"]["conditions"]
        if "entitlement" in condition["id"]
    }
    assert entitlement_checks == {"ew.pytest.entitlement", "ds.cargo.lib", "ip.pytest.entitlement"}


def test_open_release_issue_blocks_readiness_but_never_erases_evidence():
    catalogue, task, records = _release_fixture()
    issue = {
        "repo": "app", "number": 38, "title": "Wrong total", "url": "https://example.test/38",
        "labels": ["bug"],
    }

    clear = task_status(
        task, records, {"app": HEAD}, catalogue, catalogue["release"],
        release_issues={"app": []}, issue_unavailable_repos=set(),
    )
    blocked = task_status(
        task, records, {"app": HEAD}, catalogue, catalogue["release"],
        release_issues={"app": [issue]}, issue_unavailable_repos=set(),
    )

    assert clear.maturity == "ready for release" and clear.release_issue_gate == "clear"
    assert blocked.maturity == "demonstrated" and blocked.release_issue_gate == "blocked"
    assert blocked.release_issue_blockers == [issue]
    assert all(condition.state == "satisfied" for condition in blocked.conditions)


def test_unavailable_issue_source_blocks_readiness_instead_of_looking_empty():
    catalogue, task, records = _release_fixture()
    status = task_status(
        task, records, {"app": HEAD}, catalogue, catalogue["release"],
        release_issues={"app": []}, issue_unavailable_repos={"app"},
    )
    assert status.maturity == "demonstrated"
    assert status.release_issue_gate == "unavailable"


def test_render_only_pre_feature_state_fails_issue_gate_closed():
    catalogue = {
        "release": {"issue_gate": {"milestone": "First Public Release"}},
        "repos": {"app": {}, "contracts": {}},
    }

    issues, unavailable = release_issue_state(catalogue, {"runs": 1})
    assert issues == {}
    assert unavailable == {"app", "contracts"}

    issues, unavailable = release_issue_state(
        catalogue,
        {"release_issues": {"app": []}, "release_issue_unavailable_repos": []},
    )
    assert issues == {"app": []}
    assert unavailable == set()


def test_issue_collection_keeps_only_open_first_release_milestone_and_is_fail_loud():
    class GitHub:
        def open_items(self, repo: str, kind: str):
            assert kind == "issues"
            if repo == "broken/repo":
                return Failure("github_issues", "timed out")
            return [
                {"number": 4, "title": "Release defect", "html_url": "https://example.test/4",
                 "milestone": {"title": "First Public Release"}, "labels": [{"name": "bug"}]},
                {"number": 5, "title": "Later automation", "html_url": "https://example.test/5",
                 "milestone": {"title": "Later"}, "labels": [{"name": "automation"}]},
            ]

    catalogue = {
        "release": {"issue_gate": {"milestone": "First Public Release"}},
        "repos": {
            "app": {"github": "owner/app"},
            "broken": {"github": "broken/repo"},
        },
    }
    state = {"release_issues": {"broken": [{"number": 3, "title": "Last known"}]}}
    failures: list[dict] = []

    issues, unavailable = collect_release_issues(GitHub(), catalogue, state, failures)

    assert issues["app"] == [{
        "repo": "app", "number": 4, "title": "Release defect",
        "url": "https://github.com/owner/app/issues/4",
        "labels": ["bug"],
    }]
    assert issues["broken"] == [{"number": 3, "title": "Last known"}]
    assert unavailable == {"broken"}
    assert failures == [{"repo": "broken", "what": "github_issues", "why": "timed out"}]


def test_release_blocker_is_visible_and_html_escaped():
    catalogue, task, records = _release_fixture()
    task.update({"title": "Release", "promise": "Ship", "depends_on": []})
    issue = {
        "repo": "app", "number": 38, "title": "<bad> total", "url": "https://example.test/38",
        "labels": ["bug"],
    }
    status = task_status(
        task, records, {"app": HEAD}, catalogue, catalogue["release"],
        release_issues={"app": [issue]}, issue_unavailable_repos=set(),
    )
    payload = status_payload(
        {"release": {"target": "apps", "automate_scope": {"decision": "deferred", "note": "later"}}},
        [status], {}, [], {"runs": 1, "last_run_at": "2026-09-12T00:00:00Z"}, {}, records,
    )

    assert payload["tasks"][0]["release_issue_blockers"] == [issue]
    report = report_md(payload, {"release": payload["release"]})
    dashboard = dashboard_html(payload, {"release": payload["release"]})
    assert "app#38" in report and "Release blockers" in report
    assert "<bad> total" not in report and "&lt;bad&gt; total" in report
    assert "<bad> total" not in dashboard
    assert "${esc(issue.title)}" in dashboard
