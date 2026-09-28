"""The first-release contract keeps capability proof and issue-gate evidence distinct."""

from __future__ import annotations

import json
import re
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


AUTOMATIONS = [
    "ew.meeting_suggestions_and_confirmed_write",
    "automate.unattended_pdf_summary",
    "automate.invoice_intake_and_digest",
]
BUNDLE_REPOS = {
    "eom-email-watcher", "document-summarizer", "invoice-processor", "document-ocr", "connect-contracts",
}
PRODUCT_STEMS = ("download", "licence_online", "without_licence", "privacy")
# Contract 09 B3: what the bundle row gains, beyond its 17 original conditions.
BUNDLE_ADDED = {
    "rel.bundle_auto1_extraction", "rel.bundle_auto1_gated", "rel.bundle_auto1_linux", "rel.bundle_auto1_windows",
    *(f"rel.bundle_auto{n}_{os}" for n in (2, 3) for os in ("linux", "windows")),
    *(f"rel.bundle_{stem}_{os}" for stem in PRODUCT_STEMS for os in ("linux", "windows")),
    "rel.bundle_automate_issue_gate", "rel.bundle_ocr_issue_gate",
}
HEADS = {
    "eom-email-watcher": "a" * 40, "document-summarizer": "b" * 40, "invoice-processor": "c" * 40,
    "connect-contracts": "d" * 40, "connect-automate": "e" * 40, "document-ocr": "f" * 40,
}


def _passing_record(catalogue: dict, condition: dict) -> Record:
    """A record exactly as the writers build one for this condition at HEADS."""
    check = catalogue["checks"][condition["check"]]
    repo = check["repo"]
    kind = condition["kind"]
    return Record(
        kind=kind, repo=repo, revision=HEADS[repo], verdict="pass",
        platform=condition.get("platform", check.get("platform", "n/a")),
        condition_ids=[condition["id"]],
        participants={name: HEADS[name] for name in check.get("participants", [])},
        source={
            "check": condition["check"],
            "check_fingerprint": check_fingerprint(check),
            "condition_fingerprints": {condition["id"]: condition_fingerprint(condition)},
        },
        detail={"milestone": "First Public Release", "issues": []} if kind == "issue_gate" else {},
        executed=1 if kind in ("automated_test", "ci_run") else None,
        failed=0 if kind in ("automated_test", "ci_run") else None,
    )


def _broken_catalogue(tmp_path: Path, mutate) -> Path:
    catalogue = json.loads((ROOT / "catalogue.json").read_text())
    mutate(catalogue)
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(catalogue))
    return path


def test_first_release_is_the_bundle_with_its_automations():
    catalogue = load(ROOT / "catalogue.json")
    scope = catalogue["release"]["automate_scope"]
    assert scope["required_for_first_release"] is True
    assert scope["tasks"] == AUTOMATIONS
    assert catalogue["release"]["issue_gate"]["milestone"] == "First Public Release"
    tasks = {task["id"]: task for task in catalogue["tasks"]}
    checks = catalogue["checks"]
    release_tasks = {tid for tid, task in tasks.items() if task["layer"] == "release"}
    assert release_tasks == {
        "release.email_watcher", "release.document_summarizer", "release.invoice_processor",
        "release.local_connect_bundle",
    }
    bundle = {condition["id"]: condition for condition in tasks["release.local_connect_bundle"]["conditions"]}
    carried = {condition["check"] for condition in bundle.values()}
    for name in AUTOMATIONS:
        for condition in tasks[name]["conditions"]:
            if condition["kind"] != "source_inspection":
                assert condition["check"] in carried, (name, condition["check"])
    for stem in PRODUCT_STEMS:
        for os in ("linux", "windows"):
            condition = bundle[f"rel.bundle_{stem}_{os}"]
            assert condition["kind"] == "installed_demo" and condition["platform"] == os
            assert set(checks[condition["check"]]["participants"]) == BUNDLE_REPOS
    # A scan is read wherever contract 09 says so, and OCR's revision is bound to it.
    scanning = [
        *(f"auto.pdf_summary_installed_{os}" for os in ("linux", "windows")),
        *(f"auto.invoice_digest_installed_{os}" for os in ("linux", "windows")),
        *(f"rel.{app}_shared_runtime_{os}" for app in ("ds", "ip") for os in ("linux", "windows")),
        "rel.bundle_without_licence_linux", "rel.bundle_without_licence_windows",
    ]
    by_id = {c["id"]: c for task in tasks.values() for c in task["conditions"]}
    for condition_id in scanning:
        condition = by_id[condition_id]
        assert "scanned PDF" in condition["proves"], condition_id
        assert "document-ocr" in checks[condition["check"]]["participants"], condition_id
    assert "participants" not in checks["manual.ew_shared_runtime_linux"]
    for app, task_id in (("ew", "release.email_watcher"), ("ds", "release.document_summarizer"),
                         ("ip", "release.invoice_processor")):
        ids = {c["id"] for c in tasks[task_id]["conditions"]}
        assert {f"rel.{app}_shared_runtime_linux", f"rel.{app}_shared_runtime_windows"} <= ids
    issue_checks = {c["check"] for c in bundle.values() if c["kind"] == "issue_gate"}
    assert issue_checks == {
        "release.issues.ew", "release.issues.ds", "release.issues.ip", "release.issues.contracts",
        "release.issues.automate", "release.issues.ocr",
    }
    entitlement_checks = {c["check"] for cid, c in bundle.items() if "entitlement" in cid}
    assert entitlement_checks == {"ew.pytest.entitlement", "ds.cargo.lib", "ip.pytest.entitlement"}
    assert {"connect-automate", "document-ocr"} <= set(catalogue["repos"])


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c["release"]["automate_scope"].pop("tasks"), "non-empty list of distinct task ids"),
        (lambda c: c["release"]["automate_scope"].__setitem__("tasks", []), "non-empty list of distinct task ids"),
        (lambda c: c["release"]["automate_scope"].__setitem__("tasks", AUTOMATIONS + AUTOMATIONS[:1]),
         "non-empty list of distinct task ids"),
        (lambda c: c["release"]["automate_scope"].__setitem__("tasks", ["no.such.task"]), "not an automate task"),
        (lambda c: c["release"]["automate_scope"].__setitem__("tasks", ["release.email_watcher"]),
         "not an automate task"),
        (lambda c: c.__setitem__("tasks", [t for t in c["tasks"] if t["id"] != "release.local_connect_bundle"]),
         "exactly one bundle release row, found 0"),
        (lambda c: next(t for t in c["tasks"] if t["id"] == "release.local_connect_bundle")["conditions"].__setitem__(
            slice(None),
            [x for x in next(t for t in c["tasks"] if t["id"] == "release.local_connect_bundle")["conditions"]
             if x["check"] != "manual.auto_pdf_summary_windows"]),
         "does not carry manual.auto_pdf_summary_windows from automate.unattended_pdf_summary"),
        (lambda c: next(t for t in c["tasks"] if t["id"] == "automate.invoice_intake_and_digest")["conditions"].__setitem__(
            slice(None),
            [x for x in next(t for t in c["tasks"] if t["id"] == "automate.invoice_intake_and_digest")["conditions"]
             if x["id"] != "auto.invoice_digest_installed_windows"]),
         "automate.invoice_intake_and_digest has no installed demonstration on windows"),
    ],
)
def test_catalogue_rejects_an_automation_set_the_bundle_does_not_carry(tmp_path: Path, mutate, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        load(_broken_catalogue(tmp_path, mutate))


@pytest.mark.parametrize("flag", ["true", 1, 0, "yes", "", [], {}])
def test_a_non_boolean_requirement_flag_is_refused_not_ignored(tmp_path: Path, flag):
    def mutate(catalogue):
        catalogue["release"]["automate_scope"]["required_for_first_release"] = flag
        # the same edit that a real `true` refuses (a missing Windows demo) must not slip through
        task = next(t for t in catalogue["tasks"] if t["id"] == "automate.unattended_pdf_summary")
        task["conditions"] = [c for c in task["conditions"] if c["id"] != "auto.pdf_summary_installed_windows"]

    with pytest.raises(ValueError, match="required_for_first_release must be true or false"):
        load(_broken_catalogue(tmp_path, mutate))


@pytest.mark.parametrize("flag", [None, False])
def test_an_undecided_or_false_flag_skips_the_automation_set(tmp_path: Path, flag):
    def mutate(catalogue):
        catalogue["release"]["automate_scope"]["required_for_first_release"] = flag
        catalogue["release"]["automate_scope"].pop("tasks")

    assert load(_broken_catalogue(tmp_path, mutate))


def test_a_malformed_automate_scope_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="automate_scope must be an object"):
        load(_broken_catalogue(tmp_path, lambda c: c["release"].__setitem__("automate_scope", "required")))


def test_an_automation_set_is_checked_only_when_required(tmp_path: Path):
    assert load(ROOT / "catalogue.json")  # the committed catalogue passes

    def not_required(catalogue):
        catalogue["release"]["automate_scope"]["required_for_first_release"] = False
        catalogue["release"]["automate_scope"].pop("tasks")
        bundle = next(t for t in catalogue["tasks"] if t["id"] == "release.local_connect_bundle")
        bundle["conditions"] = [c for c in bundle["conditions"] if not c["id"].startswith("rel.bundle_auto")]

    assert load(_broken_catalogue(tmp_path, not_required))

    def second_bundle(catalogue):
        bundle = next(t for t in catalogue["tasks"] if t["id"] == "release.local_connect_bundle")
        twin = json.loads(json.dumps(bundle))
        twin["id"] = "release.twin"
        for condition in twin["conditions"]:
            condition["id"] = "twin." + condition["id"]
        catalogue["tasks"].append(twin)

    with pytest.raises(ValueError, match="exactly one bundle release row, found 2"):
        load(_broken_catalogue(tmp_path, second_bundle))


def test_an_automated_condition_added_to_an_automation_must_be_carried(tmp_path: Path):
    def add(check_id):
        def mutate(catalogue):
            task = next(t for t in catalogue["tasks"] if t["id"] == "automate.unattended_pdf_summary")
            task["conditions"].append({
                "id": "auto.rule_unit", "kind": "automated_test", "check": check_id, "proves": "a unit test",
            })
        return mutate

    # a check the bundle row does not carry is refused ...
    with pytest.raises(ValueError, match="does not carry ew.pytest.notify from automate.unattended_pdf_summary"):
        load(_broken_catalogue(tmp_path, add("ew.pytest.notify")))
    # ... and one it already carries (automation 1's) is accepted
    assert load(_broken_catalogue(tmp_path, add("ew.pytest.automation")))


def test_old_bundle_evidence_does_not_make_the_new_bundle_ready():
    catalogue = load(ROOT / "catalogue.json")
    bundle = next(t for t in catalogue["tasks"] if t["id"] == "release.local_connect_bundle")
    proving = [c for c in bundle["conditions"] if c["kind"] != "release_artifact"]
    original = [c for c in proving if c["id"] not in BUNDLE_ADDED]
    assert len(original) + 3 == 17  # the 17 original conditions include 3 release artifacts
    records = [_passing_record(catalogue, c) for c in original]
    status = task_status(bundle, records, HEADS, catalogue, catalogue["release"])
    assert status.maturity not in ("ready for release", "released")
    unmet = {c.condition["id"] for c in status.conditions
             if c.state != "satisfied" and c.condition["kind"] != "release_artifact"}
    assert unmet == BUNDLE_ADDED

    records += [_passing_record(catalogue, c) for c in proving if c["id"] in BUNDLE_ADDED]
    ready = task_status(bundle, records, HEADS, catalogue, catalogue["release"])
    assert ready.maturity == "ready for release"


def test_an_automation_demo_record_satisfies_both_rows():
    catalogue = load(ROOT / "catalogue.json")
    check_id = "manual.auto_pdf_summary_linux"
    check = catalogue["checks"][check_id]
    # record_observation.py names every condition that uses the check (scripts/record_observation.py)
    conditions = [c for t in catalogue["tasks"] for c in t["conditions"] if c["check"] == check_id]
    assert {c["id"] for c in conditions} == {"auto.pdf_summary_installed_linux", "rel.bundle_auto2_linux"}
    record = Record(
        kind="installed_demo", repo=check["repo"], revision=HEADS[check["repo"]], verdict="pass",
        platform="linux", condition_ids=[c["id"] for c in conditions],
        participants={name: HEADS[name] for name in check["participants"]},
        source={
            "type": "manual_observation", "check": check_id, "check_fingerprint": check_fingerprint(check),
            "condition_fingerprints": {c["id"]: condition_fingerprint(c) for c in conditions},
        },
    )
    tasks = {t["id"]: t for t in catalogue["tasks"]}
    for task_id, condition_id in (("automate.unattended_pdf_summary", "auto.pdf_summary_installed_linux"),
                                  ("release.local_connect_bundle", "rel.bundle_auto2_linux")):
        status = task_status(tasks[task_id], [record], HEADS, catalogue, catalogue["release"])
        state = next(c.state for c in status.conditions if c.condition["id"] == condition_id)
        assert state == "satisfied", (task_id, state)


# Contract 09 I5: commercial terms are private until launch (invoice-processor#94).
CURRENCY = re.compile(
    r"(?<![\w$])[$\u20ac\u00a3]\s?\d[\d,]*(?:\.\d+)?(?![\w\"'})])"
    r"|\b\d+(?:\.\d+)?\s?(?:USD|EUR|GBP|dollars?|euros?)\b"
    r"|\bper (?:month|year|seat|user)\b|/(?:mo|month|yr|year)\b",
    re.I,
)
PERIOD = re.compile(r"\b\d+[- ]?(?:days?|months?)\b", re.I)
SCANNED = ("catalogue.json", "README.md", "AGENTS.md", "docs", "lcstatus", "scripts", "systemd", "tests")


def commercial_terms(text: str) -> list[str]:
    return [m.group(0) for rx in (CURRENCY, PERIOD) for m in rx.finditer(text)]


def test_no_commercial_terms_in_the_repository():
    import subprocess

    files = subprocess.run(["git", "ls-files", *SCANNED], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split()
    assert "catalogue.json" in files and "docs/RELEASE_CONTRACT.md" in files
    found = {}
    for name in files:
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if hits := commercial_terms(text):
            found[name] = hits
    assert found == {}


@pytest.mark.parametrize(
    ("line", "caught"),
    [
        ("The plan costs " + "$" + "12 a seat.", True),
        ("Pay 9.99 " + "USD" + " now.", True),
        ("Billed per " + "month.", True),
        ("A 30" + "-day window.", True),
        ("Renews every 12" + " months.", True),
        ('HERE="$(cd "$(dirname "$' + '0")" && pwd)"', False),
        ("The server stops 10 to 12 seconds after the last lease.", False),
        ("Automation 2 is demonstrated on the installed Linux apps.", False),
    ],
)
def test_the_commercial_terms_scan_catches_each_kind(line: str, caught: bool):
    assert bool(commercial_terms(line)) is caught


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
        found = {
            "owner/app": [{"title": "First Public Release", "number": 1, "state": "open", "open_issues": 1},
                          {"title": "Later", "number": 2, "state": "open", "open_issues": 1}],
            "owner/clear": [{"title": "First Public Release", "number": 1, "state": "open", "open_issues": 0},
                            {"title": "Later", "number": 2, "state": "open", "open_issues": 1}],
            "broken/repo": [{"title": "First Public Release", "number": 1, "state": "open", "open_issues": 0}],
        }

        def milestones(self, repo: str):
            return self.found[repo]

        def open_issues_and_pulls(self, repo: str):
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
