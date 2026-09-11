"""The failure cases the report must get right. Each test is one way the dashboard could lie."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lcstatus.change import assess
from lcstatus.evidence import Record, Store, check_fingerprint
from lcstatus.rules import condition_status as _condition_status, task_status

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
CHECK_FOR_CONDITION = {
    "c1": "t.pytest",
    "c2": "t.ci.win",
    "c9": "t.ci.win",
    "d1": "t.demo",
    "i1": "t.inspect",
    "r1": "t.rel",
}


def task(conds, layer="connect"):
    return {"id": "t", "layer": layer, "app": "x", "app_repo": "ip", "conditions": conds, "depends_on": []}


def condition_status(condition, records, heads, check_repo):
    return _condition_status(
        condition, records, heads, check_repo, CAT["checks"][condition["check"]]
    )


def rec(kind, verdict, revision=NEW, cond="c1", **kw):
    kw.setdefault("repo", "ip")
    kw.setdefault("revision_time", "2026-09-09T10:00:00+00:00" if revision == NEW else "2026-09-01T10:00:00+00:00")
    check_id = CHECK_FOR_CONDITION[cond]
    check = CAT["checks"][check_id]
    kw.setdefault("platform", check.get("platform", "n/a"))
    kw.setdefault("source", {
        "check": check_id,
        "check_fingerprint": check_fingerprint(check),
    })
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


def test_condition_evidence_must_come_from_its_current_configured_check():
    stale = rec(
        "automated_test", "pass", executed=3, failed=0, platform="linux",
        source={"type": "local_runner", "check": "t.replaced"},
    )
    current = rec("automated_test", "pass", executed=3, failed=0, platform="linux")
    condition = {"id": "c1", "kind": "automated_test", "check": "t.pytest"}

    rejected = condition_status(condition, [stale], {"ip": NEW}, "ip")
    accepted = condition_status(condition, [stale, current], {"ip": NEW}, "ip")

    assert rejected.state == "no_evidence"
    assert accepted.state == "satisfied" and accepted.current is current


def test_condition_evidence_must_match_the_current_check_configuration():
    current_check = CAT["checks"]["t.pytest"]
    former_check = dict(current_check, args=["tests/former.py"])
    stale = rec(
        "automated_test", "pass", executed=3, failed=0, platform="linux",
        source={
            "type": "local_runner",
            "check": "t.pytest",
            "check_fingerprint": check_fingerprint(former_check),
        },
    )
    current = rec("automated_test", "pass", executed=3, failed=0, platform="linux")
    current_task = task([{"id": "c1", "kind": "automated_test", "check": "t.pytest"}])

    rejected = task_status(current_task, [stale], {"ip": NEW}, CAT, CAT["release"])
    accepted = task_status(current_task, [stale, current], {"ip": NEW}, CAT, CAT["release"])

    assert rejected.conditions[0].state == "no_evidence"
    assert accepted.conditions[0].state == "satisfied"


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


def test_check_platform_filters_mixed_evidence_when_condition_omits_platform():
    t = task([{"id": "c2", "kind": "ci_run", "check": "t.ci.win"}])
    linux_pass = rec("ci_run", "pass", cond="c2", platform="linux")
    windows_unavailable = rec("ci_run", "unavailable", cond="c2", platform="windows")

    status = task_status(t, [linux_pass, windows_unavailable], {"ip": NEW}, CAT, CAT["release"])

    condition = status.conditions[0]
    assert condition.platform == "windows"
    assert condition.state == "not_checked"
    assert condition.current is windows_unavailable


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
    recs = [rec("automated_test", "pass", executed=1, failed=0, platform="linux"),
            rec("installed_demo", "pass", cond="d1", participants={"ip": NEW, "ew": NEW}),
            rec("release_artifact", "fail", cond="r1", summary="no published release")]
    s = task_status(t, recs, {"ip": NEW, "ew": NEW}, CAT, CAT["release"])
    assert s.maturity == "demonstrated"


@pytest.mark.parametrize("missing_id", ["rel.ip_licence_activation", "rel.ip_first_run_model"])
def test_release_promise_requires_licence_and_first_run_model_evidence(missing_id):
    from lcstatus.catalogue import load

    catalogue = load(Path(__file__).resolve().parent.parent / "catalogue.json")
    release_task = next(item for item in catalogue["tasks"] if item["id"] == "release.linux_and_windows")
    checks = catalogue["checks"]
    conditions = {condition["id"]: condition for condition in release_task["conditions"]}
    assert conditions["rel.ip_licence_activation"]["check"] == "ip.pytest.entitlement"
    assert conditions["rel.ip_first_run_model"]["check"] == "ip.pytest.first_run_model"
    assert checks["ip.pytest.first_run_model"]["args"] == [
        "tests/test_shell.py::test_first_run_names_the_recommended_model"
    ]
    heads = {
        "eom-email-watcher": "a" * 40,
        "document-summarizer": "b" * 40,
        "invoice-processor": "c" * 40,
    }

    def evidence_for(condition):
        check = checks[condition["check"]]
        repo = check["repo"]
        participants = {name: heads[name] for name in check.get("participants", [])}
        return Record(
            kind=condition["kind"], repo=repo, revision=heads[repo], verdict="pass",
            platform=condition.get("platform", check.get("platform", "n/a")),
            condition_ids=[condition["id"]], participants=participants,
            source={"check": condition["check"], "check_fingerprint": check_fingerprint(check)},
            executed=1 if condition["kind"] in ("automated_test", "ci_run") else None,
            failed=0 if condition["kind"] in ("automated_test", "ci_run") else None,
        )

    records = [evidence_for(condition) for condition in release_task["conditions"] if condition["id"] != missing_id]
    blocked = task_status(release_task, records, heads, catalogue, catalogue["release"])
    assert blocked.maturity != "released"
    assert next(c for c in blocked.conditions if c.condition["id"] == missing_id).state == "no_evidence"

    records.append(evidence_for(conditions[missing_id]))
    released = task_status(release_task, records, heads, catalogue, catalogue["release"])
    assert released.maturity == "released"


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
               revision_time="2026-09-09T10:00:00+00:00", summary="no published release",
               source={"check": "t.rel", "check_fingerprint": check_fingerprint(CAT["checks"]["t.rel"])})
    s = task_status(t, [r], {"ip": NEW}, CAT, CAT["release"])
    assert s.conditions[0].state == "check_failed" and s.maturity == "planned"


def test_task_with_only_inspection_conditions_is_not_checked_not_current():
    t = task([{"id": "i1", "kind": "source_inspection", "check": "t.inspect"}], layer="automate")
    s = task_status(t, [], {"ew": NEW, "ip": NEW}, CAT, CAT["release"])
    assert s.maturity == "planned" and s.freshness == "no_evidence"


def test_missing_release_is_not_a_failing_check_for_freshness():
    t = task([{"id": "c1", "kind": "automated_test", "check": "t.pytest"},
              {"id": "r1", "kind": "release_artifact", "check": "t.rel"}], layer="release")
    recs = [rec("automated_test", "pass", executed=1, failed=0, platform="linux"),
            Record(kind="release_artifact", repo="ip", revision=NEW, verdict="fail", condition_ids=["r1"],
                   revision_time="2026-09-09T10:00:00+00:00", summary="no published release",
                   source={"check": "t.rel", "check_fingerprint": check_fingerprint(CAT["checks"]["t.rel"])})]
    s = task_status(t, recs, {"ip": NEW}, CAT, CAT["release"])
    assert s.conditions[1].state == "check_failed" and s.freshness == "not_checked" and s.maturity == "built"


def test_no_record_at_all_is_no_evidence_not_not_checked():
    """"Not checked" must mean a check ran and gave no result; silence is "no evidence"."""
    s = condition_status({"id": "c9", "kind": "ci_run", "check": "t.ci.win", "platform": "windows"}, [], {"ip": NEW}, "ip")
    assert s.state == "no_evidence" and s.current is None and s.last_proven is None
    skipped = rec("ci_run", "unavailable", cond="c9", platform="windows")
    s2 = condition_status({"id": "c9", "kind": "ci_run", "check": "t.ci.win", "platform": "windows"}, [skipped], {"ip": NEW}, "ip")
    assert s2.state == "not_checked" and s2.current is skipped


def test_without_a_current_head_stored_evidence_is_history_not_current():
    """A head lookup failure leaves the head unknown; passing evidence at the last known SHA
    must read "changed since", never "verified at current code"."""
    r = rec("automated_test", "pass", executed=5, failed=0)
    s = condition_status({"id": "c1", "kind": "automated_test", "check": "t.pytest"}, [r], {}, "ip")
    assert s.state == "changed_since" and s.current is None and s.last_proven is r


def test_release_of_older_code_is_changed_since_once_main_moves_on():
    t = task([{"id": "r1", "kind": "release_artifact", "check": "t.rel"}], layer="release")
    old_release = Record(kind="release_artifact", repo="ip", revision=OLD, verdict="pass", condition_ids=["r1"],
                         revision_time="2026-09-01T00:00:00+00:00", summary="v1",
                         source={"check": "t.rel", "check_fingerprint": check_fingerprint(CAT["checks"]["t.rel"])})
    s = task_status(t, [old_release], {"ip": NEW}, CAT, CAT["release"])
    assert s.conditions[0].state == "changed_since" and s.maturity != "released"


# --- regressions from PR review: observation history and repository isolation -----------

def test_pass_fail_pass_recovery_is_retained_and_wins_after_reload(tmp_path: Path):
    st = Store(tmp_path / "r.jsonl")
    first = rec("automated_test", "pass", executed=2, failed=0,
                recorded_at="2026-09-11T10:00:00+00:00")
    failed = rec("automated_test", "fail", executed=2, failed=1, exit_code=1,
                 recorded_at="2026-09-11T10:00:01+00:00")
    recovered = rec("automated_test", "pass", executed=2, failed=0,
                    recorded_at="2026-09-11T10:00:02+00:00")

    assert st.add(first) and st.add(failed) and st.add(recovered)
    loaded = Store(tmp_path / "r.jsonl")
    assert len(loaded) == 3
    assert len({item.record_id for item in loaded.all()}) == 3

    status = condition_status(
        {"id": "c1", "kind": "automated_test", "check": "t.pytest"},
        loaded.all(),
        {"ip": NEW},
        "ip",
    )
    assert status.state == "satisfied"
    assert status.current is not None and status.current.record_id == recovered.record_id


def test_foreign_repository_release_record_cannot_satisfy_app_condition():
    invoice_missing = rec(
        "release_artifact", "fail", cond="r1", repo="ip",
        recorded_at="2026-09-11T10:00:00+00:00",
    )
    unrelated_release = rec(
        "release_artifact", "pass", cond="r1", repo="ew",
        recorded_at="2026-09-11T11:00:00+00:00",
    )
    status = condition_status(
        {"id": "r1", "kind": "release_artifact", "check": "t.rel"},
        [invoice_missing, unrelated_release],
        {"ip": NEW, "ew": NEW},
        "ip",
    )
    assert status.state == "check_failed"
    assert status.current is invoice_missing


def test_source_inspection_exposes_current_hint_without_proving_behavior():
    hint = rec(
        "source_inspection", "inconclusive", cond="i1", repo="ew",
        summary="inspection only: 2/2 configured markers found",
    )
    status = condition_status(
        {"id": "i1", "kind": "source_inspection", "check": "t.inspect"},
        [hint],
        {"ew": NEW},
        "ew",
    )
    assert status.state == "inconclusive"
    assert status.current is hint
    assert status.last_proven is None


def test_current_catalogue_has_evidence_driven_status_copy_and_source_checks():
    from lcstatus.catalogue import load

    catalogue = load(Path(__file__).resolve().parent.parent / "catalogue.json")
    assert all("unfinished" not in task and "next_action" not in task for task in catalogue["tasks"])
    source_checks = [check for check in catalogue["checks"].values() if check["runner"] == "source_inspection"]
    assert source_checks
    assert all(check["paths"] and check["markers"] for check in source_checks)


def test_catalogue_rejects_source_condition_wired_to_manual_runner(tmp_path: Path):
    from lcstatus.catalogue import load

    catalogue = {
        "repos": {"ew": {"github": "example/ew"}},
        "apps": {"watcher": {"repo": "ew"}},
        "checks": {"inspect": {"runner": "manual_observation", "repo": "ew"}},
        "tasks": [{
            "id": "task", "app": "watcher", "layer": "automate",
            "conditions": [{"id": "condition", "kind": "source_inspection", "check": "inspect"}],
        }],
    }
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(catalogue))
    with pytest.raises(ValueError, match="cannot use 'manual_observation' runner"):
        load(path)



def test_current_release_failure_beats_old_pass_with_inflated_publication_time():
    old_pass = rec(
        "release_artifact", "pass", cond="r1", repo="ip",
        revision_time="2026-09-11T20:00:00+00:00",
        recorded_at="2026-09-11T10:00:00+00:00",
    )
    current_failure = rec(
        "release_artifact", "fail", cond="r1", repo="ip",
        revision_time="2026-09-11T09:00:00+00:00",
        recorded_at="2026-09-11T11:00:00+00:00",
    )
    status = condition_status(
        {"id": "r1", "kind": "release_artifact", "check": "t.rel"},
        [old_pass, current_failure],
        {"ip": NEW},
        "ip",
    )
    assert status.state == "check_failed"
    assert status.current is current_failure


def test_backfilled_older_manual_pass_cannot_displace_newer_observed_failure():
    newer_failure = rec(
        "installed_demo", "fail", cond="d1", repo="ip",
        recorded_at="2026-09-11T16:00:00+00:00",
    )
    backfilled_pass = rec(
        "installed_demo", "pass", cond="d1", repo="ip",
        recorded_at="2026-09-11T15:00:00+00:00",
    )
    status = condition_status(
        {"id": "d1", "kind": "installed_demo", "check": "t.demo"},
        [newer_failure, backfilled_pass],
        {"ip": NEW},
        "ip",
    )
    assert status.state == "check_failed"
    assert status.current is newer_failure


def test_last_proven_uses_absolute_instant_across_offsets():
    lexically_later_but_older = rec(
        "automated_test", "pass", revision=OLD, executed=1, failed=0,
        revision_time="2026-09-11T10:00:00+02:00",
        recorded_at="2026-09-11T10:01:00+02:00",
    )
    lexically_earlier_but_newer = rec(
        "automated_test", "pass", revision=NEW, executed=1, failed=0,
        revision_time="2026-09-11T09:30:00+00:00",
        recorded_at="2026-09-11T09:31:00+00:00",
    )
    status = condition_status(
        {"id": "c1", "kind": "automated_test", "check": "t.pytest"},
        [lexically_later_but_older, lexically_earlier_but_newer],
        {},
        "ip",
    )
    assert status.state == "changed_since"
    assert status.last_proven is lexically_earlier_but_newer


def test_store_latest_revision_uses_absolute_instant_across_offsets(tmp_path: Path):
    store = Store(tmp_path / "r.jsonl")
    older = Record(
        kind="revision", repo="ip", revision="a" * 40, verdict="pass",
        revision_time="2026-09-11T10:00:00+02:00",
        recorded_at="2026-09-11T10:01:00+02:00",
    )
    newer = Record(
        kind="revision", repo="ip", revision="b" * 40, verdict="pass",
        revision_time="2026-09-11T09:30:00+00:00",
        recorded_at="2026-09-11T09:31:00+00:00",
    )
    assert store.add(older) and store.add(newer)
    assert Store(tmp_path / "r.jsonl").latest_revision("ip") == newer


def test_changed_incomplete_release_is_not_deduplicated(tmp_path: Path):
    store = Store(tmp_path / "r.jsonl")
    first = Record(
        kind="release_artifact", repo="ip", revision=NEW, verdict="fail",
        condition_ids=["r1"], source={"type": "github_releases"},
        summary="v1 published but missing: windows installer, linux package",
        detail={"tag": "v1", "assets": ["SHA256SUMS"],
                "missing": ["windows installer", "linux package"]},
    )
    changed = Record(
        kind="release_artifact", repo="ip", revision=NEW, verdict="fail",
        condition_ids=["r1"], source={"type": "github_releases"},
        summary="v1 published but missing: linux package",
        detail={"tag": "v1", "assets": ["setup.exe", "SHA256SUMS"],
                "missing": ["linux package"]},
    )
    duplicate = Record.from_dict(json.loads(changed.to_json()))

    assert store.add(first) and store.add(changed)
    assert store.add(duplicate) is False
    loaded = Store(tmp_path / "r.jsonl").all()
    assert len(loaded) == 2
    assert loaded[-1].detail["assets"] == ["setup.exe", "SHA256SUMS"]


def test_bundle_readiness_requires_each_unproven_installer_observation():
    from lcstatus.catalogue import load

    catalogue = load(Path(__file__).resolve().parent.parent / "catalogue.json")
    release_task = next(item for item in catalogue["tasks"] if item["id"] == "release.linux_and_windows")
    checks = catalogue["checks"]
    conditions = {item["id"]: item for item in release_task["conditions"]}
    assert conditions["rel.ip_windows_installer_demo"] == {
        "id": "rel.ip_windows_installer_demo",
        "kind": "installed_demo",
        "check": "manual.ip_windows_install",
        "platform": "windows",
        "proves": "The current Invoice Processor Windows artifact installs and opens on Windows.",
    }
    assert conditions["rel.ew_linux_installer_demo"]["check"] == "manual.ew_linux_install"
    assert conditions["rel.ds_linux"]["check"] == "ds.ci.rust"
    assert conditions["rel.ds_linux_installer_demo"]["check"] == "manual.ds_linux_install"
    assert conditions["rel.ds_windows"]["check"] == "manual.ds_windows_install"

    heads = {
        "eom-email-watcher": "a" * 40,
        "document-summarizer": "b" * 40,
        "invoice-processor": "c" * 40,
    }
    records = []
    for condition in release_task["conditions"]:
        if condition["kind"] == "release_artifact" or condition["id"] == "rel.ip_windows_installer_demo":
            continue
        check = checks[condition["check"]]
        repo = check["repo"]
        participants = {name: heads[name] for name in check.get("participants", [])}
        records.append(Record(
            kind=condition["kind"], repo=repo, revision=heads[repo], verdict="pass",
            platform=condition.get("platform", check.get("platform", "n/a")),
            condition_ids=[condition["id"]], participants=participants,
            source={"check": condition["check"], "check_fingerprint": check_fingerprint(check)},
            executed=1 if condition["kind"] in ("automated_test", "ci_run") else None,
            failed=0 if condition["kind"] in ("automated_test", "ci_run") else None,
        ))

    blocked = task_status(release_task, records, heads, catalogue, catalogue["release"])
    missing = next(item for item in blocked.conditions if item.condition["id"] == "rel.ip_windows_installer_demo")
    assert missing.state == "no_evidence"
    assert blocked.maturity != "ready for release"

    records.append(Record(
        kind="installed_demo", repo="invoice-processor", revision=heads["invoice-processor"],
        verdict="pass", platform="windows", condition_ids=["rel.ip_windows_installer_demo"],
        source={"check": "manual.ip_windows_install",
                "check_fingerprint": check_fingerprint(checks["manual.ip_windows_install"])},
    ))
    ready = task_status(release_task, records, heads, catalogue, catalogue["release"])
    assert ready.maturity == "ready for release"


def test_actions_recovery_survives_volatile_source_metadata(tmp_path: Path):
    store = Store(tmp_path / "r.jsonl")
    success_source = {
        "type": "github_actions", "check": "t.ci.win",
        "check_fingerprint": check_fingerprint(CAT["checks"]["t.ci.win"]),
        "workflow": "CI", "job": "test",
        "run_id": 101, "job_id": 202, "url": "https://example.invalid/job/202", "run_attempt": 1,
    }
    first = rec(
        "ci_run", "pass", cond="c2", platform="windows", source=success_source,
        executed=4, recorded_at="2026-09-11T10:00:00+00:00",
    )
    outage = rec(
        "ci_run", "unavailable", cond="c2", platform="windows",
        source={"type": "github_actions", "check": "t.ci.win",
                "check_fingerprint": check_fingerprint(CAT["checks"]["t.ci.win"])},
        recorded_at="2026-09-11T10:01:00+00:00",
    )
    recovery = rec(
        "ci_run", "pass", cond="c2", platform="windows", source=success_source,
        executed=4, recorded_at="2026-09-11T10:02:00+00:00",
    )

    assert store.add(first) and store.add(outage) and store.add(recovery)
    loaded = Store(tmp_path / "r.jsonl")
    assert len(loaded) == 3
    status = condition_status(
        {"id": "c2", "kind": "ci_run", "check": "t.ci.win", "platform": "windows"},
        loaded.all(), {"ip": NEW}, "ip",
    )
    assert status.state == "satisfied"
    assert status.current is not None and status.current.record_id == recovery.record_id
