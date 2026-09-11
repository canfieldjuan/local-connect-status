"""The failure cases the report must get right. Each test is one way the dashboard could lie."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lcstatus.change import assess
from lcstatus.evidence import Record, Store
from lcstatus.rules import condition_status, task_status

CAT = {
    "release": {"required_platforms": ["linux", "windows"]},
    "checks": {
        "t.pytest": {"runner": "pytest", "repo": "ip", "platform": "linux"},
        "t.ci.win": {"runner": "ci_job", "repo": "ip", "platform": "windows"},
        "t.demo": {"runner": "manual_observation", "repo": "ip", "participants": ["ip", "ew"]},
        "t.rel": {"runner": "github_release", "repo": "*"},
        "t.inspect": {"runner": "manual_observation", "repo": "ew"},
    },
}
NEW, OLD = "b" * 40, "a" * 40


def task(conds, layer="connect"):
    return {"id": "t", "layer": layer, "app": "x", "app_repo": "ip", "conditions": conds, "depends_on": []}


def rec(kind, verdict, revision=NEW, cond="c1", **kw):
    kw.setdefault("repo", "ip")
    kw.setdefault("revision_time", "2026-09-09T10:00:00+00:00" if revision == NEW else "2026-09-01T10:00:00+00:00")
    return Record(kind=kind, revision=revision, verdict=verdict, condition_ids=[cond], **kw)


# --- a failed process is failed even if it printed a passing-looking line ------------------

def test_exit_code_beats_summary_line():
    r = rec("automated_test", "fail", executed=10, failed=0, summary="10 passed in 1s", exit_code=1)
    s = condition_status({"id": "c1", "kind": "automated_test", "check": "t.pytest"}, [r], {"ip": NEW}, "ip")
    assert s.state == "check_failed"


def test_zero_executed_is_not_proof():
    r = rec("automated_test", "skip", executed=0, failed=0, exit_code=0)
    s = condition_status({"id": "c1", "kind": "automated_test", "check": "t.pytest"}, [r], {"ip": NEW}, "ip")
    assert s.state == "not_checked"


# --- evidence at an older revision is shown but never satisfies -----------------------------

def test_old_revision_evidence_is_changed_since_and_keeps_last_proven():
    r = rec("automated_test", "pass", revision=OLD, executed=5, failed=0)
    s = condition_status({"id": "c1", "kind": "automated_test", "check": "t.pytest"}, [r], {"ip": NEW}, "ip")
    assert s.state == "changed_since" and s.last_proven is r and s.current is None


def test_late_result_for_old_revision_does_not_displace_newer():
    newer = rec("automated_test", "pass", revision=NEW, executed=5, failed=0, recorded_at="2026-09-09T10:00:00+00:00")
    late_old = rec("automated_test", "fail", revision=OLD, executed=5, failed=1, recorded_at="2026-09-09T12:00:00+00:00")
    s = condition_status({"id": "c1", "kind": "automated_test", "check": "t.pytest"}, [late_old, newer], {"ip": NEW}, "ip")
    assert s.state == "satisfied" and s.current is newer


# --- cross-app evidence needs every participant at its current head ------------------------

def test_cross_app_demo_with_one_stale_participant_is_changed_since():
    r = rec("installed_demo", "pass", revision=NEW, participants={"ip": NEW, "ew": OLD}, cond="d1")
    s = condition_status({"id": "d1", "kind": "installed_demo", "check": "t.demo"}, [r], {"ip": NEW, "ew": NEW}, "ip")
    assert s.state == "changed_since"


# --- kinds do not substitute for each other --------------------------------------------------

def test_passing_test_cannot_satisfy_demo_condition():
    r = rec("automated_test", "pass", cond="d1", executed=3, failed=0)
    s = condition_status({"id": "d1", "kind": "installed_demo", "check": "t.demo"}, [r], {"ip": NEW}, "ip")
    assert s.state == "no_evidence"


def test_source_inspection_is_inconclusive_and_never_raises_maturity():
    t = task([{"id": "i1", "kind": "source_inspection", "check": "t.inspect"}], layer="automate")
    s = task_status(t, [rec("source_inspection", "pass", cond="i1", repo="ew")], {"ew": NEW, "ip": NEW}, CAT, CAT["release"])
    assert s.maturity == "planned" and s.conditions[0].state == "inconclusive"


# --- platforms are separate; linux alone is not linux-and-windows ---------------------------

def test_linux_only_evidence_leaves_windows_not_checked_and_blocks_release_readiness():
    t = task([
        {"id": "c1", "kind": "automated_test", "check": "t.pytest", "platform": "linux"},
        {"id": "c2", "kind": "ci_run", "check": "t.ci.win", "platform": "windows"},
    ], layer="release")
    s = task_status(t, [rec("automated_test", "pass", executed=4, failed=0, platform="linux")], {"ip": NEW}, CAT, CAT["release"])
    assert s.platforms == {"linux": "satisfied", "windows": "no_evidence"}
    assert s.maturity == "partly built"


# --- unavailable / pending are visible, not green ---------------------------------------------

@pytest.mark.parametrize("verdict", ["unavailable", "pending", "unknown", "partial"])
def test_non_results_are_not_checked(verdict):
    r = rec("ci_run", verdict, cond="c2", platform="windows")
    s = condition_status({"id": "c2", "kind": "ci_run", "check": "t.ci.win", "platform": "windows"}, [r], {"ip": NEW}, "ip")
    assert s.state == "not_checked" and s.current is r


# --- maturity ladder ---------------------------------------------------------------------------

def test_release_requires_release_artifact_not_just_demos():
    t = task([
        {"id": "c1", "kind": "automated_test", "check": "t.pytest"},
        {"id": "d1", "kind": "installed_demo", "check": "t.demo"},
        {"id": "r1", "kind": "release_artifact", "check": "t.rel"},
    ])
    recs = [rec("automated_test", "pass", executed=1, failed=0),
            rec("installed_demo", "pass", cond="d1", participants={"ip": NEW, "ew": NEW}),
            rec("release_artifact", "fail", cond="r1", summary="no published release")]
    s = task_status(t, recs, {"ip": NEW, "ew": NEW}, CAT, CAT["release"])
    assert s.maturity == "demonstrated"


# --- duplicates and store behaviour ---------------------------------------------------------------

def test_duplicate_delivery_stores_once(tmp_path: Path):
    st = Store(tmp_path / "r.jsonl")
    a = rec("automated_test", "pass", executed=2, failed=0)
    b = rec("automated_test", "pass", executed=2, failed=0)
    assert st.add(a) is True and st.add(b) is False and len(st) == 1
    again = Store(tmp_path / "r.jsonl")
    assert len(again) == 1


# --- change detection ------------------------------------------------------------------------------

def test_docs_only_change_is_flagged_and_unmapped_files_surface():
    tasks = [{"id": "t", "depends_on": [{"repo": "ip", "paths": ["src/**"]}]}]
    a = assess("ip", OLD, NEW, ["docs/contracts/SLICE-9.md"], tasks)
    assert a.docs_only and a.touches_contract_text and a.affected_tasks == {} and a.unmapped_files == ["docs/contracts/SLICE-9.md"]


def test_code_change_maps_to_task_and_marker_rename_alone_cannot_prove_removal():
    tasks = [{"id": "t", "depends_on": [{"repo": "ip", "paths": ["src/**"]}]}]
    a = assess("ip", OLD, NEW, ["src/invoice_processor/connect/server.py"], tasks)
    assert a.affected_tasks == {"t": ["src/invoice_processor/connect/server.py"]} and not a.docs_only
    # and a change record by itself carries no verdict about behaviour: only a fresh check can
    r = Record(kind="change", repo="ip", revision=NEW, verdict="pass", task_ids=["t"])
    s = condition_status({"id": "c1", "kind": "automated_test", "check": "t.pytest"}, [r], {"ip": NEW}, "ip")
    assert s.state == "no_evidence"


def test_invalid_verdict_or_kind_rejected():
    with pytest.raises(ValueError):
        Record(kind="automated_test", repo="ip", revision=NEW, verdict="green")
    with pytest.raises(ValueError):
        Record(kind="vibes", repo="ip", revision=NEW, verdict="pass")


# --- change records with different origins are different records ----------------------------

def test_change_records_from_different_baselines_are_both_kept(tmp_path: Path):
    st = Store(tmp_path / "r.jsonl")
    a = Record(kind="change", repo="ew", revision=NEW, verdict="pass", summary=f"{OLD[:12]} -> {NEW[:12]}: 0 files", detail={"old": OLD})
    b = Record(kind="change", repo="ew", revision=NEW, verdict="pass", summary=f"{'c'*12} -> {NEW[:12]}: 9 files", detail={"old": "c" * 40})
    assert st.add(a) and st.add(b) and len(st) == 2


def test_release_absence_is_an_explicit_not_met_at_current_head():
    t = task([{"id": "r1", "kind": "release_artifact", "check": "t.rel"}], layer="release")
    r = Record(kind="release_artifact", repo="ip", revision=NEW, verdict="fail", condition_ids=["r1"],
               revision_time="2026-09-09T10:00:00+00:00", summary="no published release")
    s = task_status(t, [r], {"ip": NEW}, CAT, CAT["release"])
    assert s.conditions[0].state == "check_failed" and s.maturity == "planned"


def test_task_with_only_inspection_conditions_is_not_checked_not_current():
    t = task([{"id": "i1", "kind": "source_inspection", "check": "t.inspect"}], layer="automate")
    s = task_status(t, [], {"ew": NEW, "ip": NEW}, CAT, CAT["release"])
    assert s.maturity == "planned" and s.freshness == "no_evidence"


def test_missing_release_is_not_a_failing_check_for_freshness():
    t = task([{"id": "c1", "kind": "automated_test", "check": "t.pytest"},
              {"id": "r1", "kind": "release_artifact", "check": "t.rel"}], layer="release")
    recs = [rec("automated_test", "pass", executed=1, failed=0),
            Record(kind="release_artifact", repo="ip", revision=NEW, verdict="fail", condition_ids=["r1"],
                   revision_time="2026-09-09T10:00:00+00:00", summary="no published release")]
    s = task_status(t, recs, {"ip": NEW}, CAT, CAT["release"])
    assert s.conditions[1].state == "check_failed" and s.freshness == "not_checked" and s.maturity == "built"


def test_no_record_at_all_is_no_evidence_not_not_checked():
    """"Not checked" must mean a check ran and gave no result; silence is "no evidence"."""
    s = condition_status({"id": "c9", "kind": "ci_run", "check": "t.ci.win", "platform": "windows"}, [], {"ip": NEW}, "ip")
    assert s.state == "no_evidence" and s.current is None and s.last_proven is None
    skipped = rec("ci_run", "unavailable", cond="c9", platform="windows")
    s2 = condition_status({"id": "c9", "kind": "ci_run", "check": "t.ci.win", "platform": "windows"}, [skipped], {"ip": NEW}, "ip")
    assert s2.state == "not_checked" and s2.current is skipped
