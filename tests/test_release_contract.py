"""The first-release contract keeps capability proof and issue-gate evidence distinct."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lcstatus.catalogue import load
from lcstatus.evidence import Record, Store, check_fingerprint, condition_fingerprint
from lcstatus.render import dashboard_html, report_md, status_payload
from lcstatus.rules import task_status
from lcstatus.sources import Failure, Revision
from lcstatus.verify import Runner


ROOT = Path(__file__).resolve().parent.parent
HEAD = "a" * 40


def _release_fixture() -> tuple[dict, dict, list[Record]]:
    catalogue = {
        "release": {"required_platforms": ["linux", "windows"]},
        "checks": {
            "linux": {"runner": "ci_job", "repo": "app", "platform": "linux"},
            "windows": {"runner": "manual_observation", "repo": "app", "platform": "windows"},
            "issues": {
                "runner": "github_issues", "repo": "app", "platform": "n/a",
                "milestone": "First Public Release",
            },
        },
    }
    task = {
        "id": "release.app",
        "app": "app",
        "app_repo": "app",
        "layer": "release",
        "conditions": [
            {"id": "linux", "kind": "ci_run", "check": "linux", "platform": "linux"},
            {"id": "windows", "kind": "installed_demo", "check": "windows", "platform": "windows"},
            {"id": "issues", "kind": "issue_gate", "check": "issues"},
        ],
    }
    records = []
    for condition in task["conditions"]:
        check = catalogue["checks"][condition["check"]]
        records.append(Record(
            kind=condition["kind"], repo="app", revision=HEAD, verdict="pass",
            platform=condition.get("platform", check.get("platform", "n/a")),
            condition_ids=[condition["id"]],
            source={
                "check": condition["check"],
                "check_fingerprint": check_fingerprint(check),
                "condition_fingerprints": {
                    condition["id"]: condition_fingerprint(condition),
                },
            },
            detail={"milestone": "First Public Release", "issues": []}
            if condition["kind"] == "issue_gate" else {},
            executed=1 if condition["kind"] == "ci_run" else None,
            failed=0 if condition["kind"] == "ci_run" else None,
        ))
    return catalogue, task, records


def _gate_record(catalogue: dict, task: dict, verdict: str, issues: list[dict] | None = None) -> Record:
    condition = next(item for item in task["conditions"] if item["kind"] == "issue_gate")
    check = catalogue["checks"][condition["check"]]
    return Record(
        kind="issue_gate", repo=check["repo"], revision=HEAD, verdict=verdict,
        condition_ids=[condition["id"]],
        source={
            "type": "github_issues",
            "check": condition["check"],
            "check_fingerprint": check_fingerprint(check),
            "condition_fingerprints": {condition["id"]: condition_fingerprint(condition)},
        },
        summary="issue gate",
        detail={"milestone": "First Public Release", "issues": issues or []},
    )


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
    issue_checks = {
        condition["check"]
        for condition in release_tasks["release.local_connect_bundle"]["conditions"]
        if condition["kind"] == "issue_gate"
    }
    assert issue_checks == {
        "release.issues.ew", "release.issues.ds", "release.issues.ip", "release.issues.contracts",
    }
    entitlement_checks = {
        condition["check"]
        for condition in release_tasks["release.local_connect_bundle"]["conditions"]
        if "entitlement" in condition["id"]
    }
    assert entitlement_checks == {"ew.pytest.entitlement", "ds.cargo.lib", "ip.pytest.entitlement"}


def test_catalogue_rejects_missing_or_mismatched_issue_gate_contract(tmp_path: Path):
    catalogue = json.loads((ROOT / "catalogue.json").read_text())
    email = next(task for task in catalogue["tasks"] if task["id"] == "release.email_watcher")
    email["conditions"] = [condition for condition in email["conditions"] if condition["kind"] != "issue_gate"]
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps(catalogue))
    with pytest.raises(ValueError, match="release task needs an issue_gate condition"):
        load(missing)

    catalogue = json.loads((ROOT / "catalogue.json").read_text())
    catalogue["checks"]["release.issues.ew"]["milestone"] = "Later"
    mismatch = tmp_path / "mismatch.json"
    mismatch.write_text(json.dumps(catalogue))
    with pytest.raises(ValueError, match="milestone must match"):
        load(mismatch)


def test_open_release_issue_blocks_readiness_but_never_erases_capability_evidence():
    catalogue, task, records = _release_fixture()
    issue = {
        "repo": "app", "number": 38, "title": "Wrong total", "url": "https://example.test/38",
        "labels": ["bug"],
    }
    blocked_records = [record for record in records if record.kind != "issue_gate"]
    blocked_records.append(_gate_record(catalogue, task, "fail", [issue]))

    clear = task_status(task, records, {"app": HEAD}, catalogue, catalogue["release"])
    blocked = task_status(task, blocked_records, {"app": HEAD}, catalogue, catalogue["release"])

    assert clear.maturity == "ready for release" and clear.release_issue_gate == "clear"
    assert blocked.maturity == "demonstrated" and blocked.release_issue_gate == "blocked"
    assert blocked.release_issue_blockers == [issue]
    assert all(
        condition.state == "satisfied"
        for condition in blocked.conditions
        if condition.condition["kind"] != "issue_gate"
    )


def test_unavailable_or_missing_issue_evidence_blocks_readiness_instead_of_looking_empty():
    catalogue, task, records = _release_fixture()
    capability_records = [record for record in records if record.kind != "issue_gate"]

    missing = task_status(task, capability_records, {"app": HEAD}, catalogue, catalogue["release"])
    unavailable = task_status(
        task,
        [*capability_records, _gate_record(catalogue, task, "unavailable")],
        {"app": HEAD}, catalogue, catalogue["release"],
    )

    assert missing.maturity == "demonstrated" and missing.release_issue_gate == "unavailable"
    assert unavailable.maturity == "demonstrated" and unavailable.release_issue_gate == "unavailable"


def test_render_only_reconstructs_latest_issue_gate_from_records(tmp_path: Path):
    catalogue, task, records = _release_fixture()
    first = {"repo": "app", "number": 4, "title": "First", "url": "https://example.test/4", "labels": []}
    second = {"repo": "app", "number": 6, "title": "Second", "url": "https://example.test/6", "labels": []}
    store = Store(tmp_path / "records.jsonl")
    store.add_all(record for record in records if record.kind != "issue_gate")
    assert store.add(_gate_record(catalogue, task, "fail", [first]))
    assert store.add(_gate_record(catalogue, task, "fail", [second]))

    reconstructed = task_status(task, store.all(), {"app": HEAD}, catalogue, catalogue["release"])
    assert reconstructed.release_issue_gate == "blocked"
    assert reconstructed.release_issue_blockers == [second]


def test_github_issue_runner_records_only_the_exact_milestone_and_fails_loud(tmp_path: Path):
    matching = {
        "number": 4, "title": "Release defect", "html_url": "https://example.test/4",
        "milestone": {"title": "First Public Release"}, "labels": [{"name": "bug"}],
    }
    later = {
        "number": 5, "title": "Later automation", "html_url": "https://example.test/5",
        "milestone": {"title": "Later"}, "labels": [{"name": "automation"}],
    }

    class GitHub:
        responses = {
            "owner/app": [matching, later],
            "owner/clear": [later],
            "broken/repo": Failure("gh", "timed out"),
        }

        def open_items(self, repo: str, kind: str):
            assert kind == "issues"
            return self.responses[repo]

    catalogue = {
        "repos": {
            "app": {"github": "owner/app"},
            "clear": {"github": "owner/clear"},
            "broken": {"github": "broken/repo"},
        },
        "tasks": [],
    }
    runner = Runner(object(), tmp_path / "cache", tmp_path / "logs", GitHub(), catalogue)
    revision = Revision("app", HEAD, "2026-09-12T00:00:00Z", "head")

    check = {"runner": "github_issues", "repo": "app", "platform": "n/a", "milestone": "First Public Release"}
    blocked = runner.release_issues("issues.app", check, revision, [], [])
    assert blocked.verdict == "fail"
    assert blocked.detail["issues"] == [{
        "repo": "app", "number": 4, "title": "Release defect",
        "url": "https://github.com/owner/app/issues/4", "labels": ["bug"],
    }]

    check = {**check, "repo": "clear"}
    clear = runner.release_issues("issues.clear", check, revision, [], [])
    assert clear.verdict == "pass" and clear.detail["issues"] == []

    check = {**check, "repo": "broken"}
    unavailable = runner.release_issues("issues.broken", check, revision, [], [])
    assert unavailable.verdict == "unavailable" and unavailable.source["type"] == "github_issues"


def test_release_blocker_is_visible_and_html_escaped():
    catalogue, task, records = _release_fixture()
    task.update({"title": "Release", "promise": "Ship", "depends_on": []})
    issue = {
        "repo": "app", "number": 38, "title": "<bad> total", "url": "https://example.test/38",
        "labels": ["bug"],
    }
    blocked_records = [record for record in records if record.kind != "issue_gate"]
    blocked_records.append(_gate_record(catalogue, task, "fail", [issue]))
    status = task_status(task, blocked_records, {"app": HEAD}, catalogue, catalogue["release"])
    payload = status_payload(
        {"release": {"target": "apps", "automate_scope": {"decision": "deferred", "note": "later"}}},
        [status], {}, [], {"runs": 1, "last_run_at": "2026-09-12T00:00:00Z"}, {}, blocked_records,
    )

    assert payload["tasks"][0]["release_issue_blockers"] == [issue]
    report = report_md(payload, {"release": payload["release"]})
    dashboard = dashboard_html(payload, {"release": payload["release"]})
    assert "app#38" in report and "Release blockers" in report
    assert "<bad> total" not in report and "&lt;bad&gt; total" in report
    assert "<bad> total" not in dashboard
    assert "${esc(issue.title)}" in dashboard
