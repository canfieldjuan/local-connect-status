"""Contract 04: the rules say only what the evidence says."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lcstatus import catalogue as catmod
from lcstatus.collect import add_record, release_targets, store_result
from lcstatus.evidence import (
    Record, Store, check_fingerprint, condition_fingerprint, instant_key, record_check_fingerprint,
    record_condition_fingerprint, resolve_check_fingerprint, validate_record_instants,
)
from lcstatus.render import COND_LABEL, condition_payload, dashboard_html, next_action, report_md, status_payload
from lcstatus.rules import ConditionStatus, TaskStatus, condition_status, task_status
from lcstatus.sources import Revision

NEW, OLD, OLDER = "b" * 40, "a" * 40, "9" * 40
T_NEW, T_OLD, T_OLDER = "2026-09-09T10:00:00+00:00", "2026-09-01T10:00:00+00:00", "2026-08-01T10:00:00+00:00"
CHECK = {"runner": "pytest", "repo": "ip", "platform": "linux", "args": ["-k", "x"]}
XCHECK = {"runner": "accept_ew_ip", "repo": "ip", "platform": "linux", "participants": ["ip", "ew"]}
CAT = {"release": {"required_platforms": ["linux"]}, "checks": {"t.pytest": CHECK, "t.xapp": XCHECK}}
COND = {"id": "c1", "kind": "automated_test", "check": "t.pytest", "proves": "it works"}
XCOND = {"id": "x1", "kind": "automated_test", "check": "t.xapp", "proves": "they work together"}
TASK = {"id": "t", "layer": "connect", "app": "x", "conditions": [COND], "depends_on": []}
HEADS = {"ip": NEW, "ew": NEW}


def rec(verdict, *, cond=COND, check=CHECK, revision=NEW, participants=None, recorded_at=None, **kw):
    times = {NEW: T_NEW, OLD: T_OLD, OLDER: T_OLDER}
    kw.setdefault("repo", "ip")
    kw.setdefault("platform", "linux")
    kw.setdefault("revision_time", times[revision])
    if recorded_at is not None:
        kw["recorded_at"] = recorded_at
    return Record(kind="automated_test", revision=revision, verdict=verdict, condition_ids=[cond["id"]],
                  participants=participants or {},
                  source={"check": cond["check"], "check_fingerprint": check_fingerprint(check),
                          "condition_fingerprints": {cond["id"]: condition_fingerprint(cond)}}, **kw)


# --- B1 --------------------------------------------------------------------------------------

def test_participant_set_must_equal_the_declared_set():
    exact = rec("pass", cond=XCOND, check=XCHECK, participants={"ip": NEW, "ew": NEW})
    assert condition_status(XCOND, [exact], HEADS, "ip", XCHECK).state == "satisfied"
    for bad in (
        {"ip": NEW},                              # subset
        {"ip": NEW, "ew": NEW, "zz": NEW},        # superset
        {},                                       # none for a check that declares some
    ):
        s = condition_status(XCOND, [rec("pass", cond=XCOND, check=XCHECK, participants=bad)], HEADS, "ip", XCHECK)
        assert s.state == "config_changed", bad
        assert s.current is None and s.last_proven is not None
    # a cross-app row that names nothing cannot prove two apps
    s = condition_status(XCOND, [rec("pass", cond=XCOND, check=XCHECK)], HEADS, "ip", XCHECK)
    assert s.state == "config_changed"
    # a check that declares none declares its own repository: a single-app demo names exactly that
    assert condition_status(COND, [rec("pass", participants={"ip": NEW})], HEADS, "ip", CHECK).state == "satisfied"
    assert condition_status(COND, [rec("pass")], HEADS, "ip", CHECK).state == "satisfied"
    assert condition_status(COND, [rec("pass", participants={"ew": NEW})], HEADS, "ip", CHECK).state == "config_changed"
    assert condition_status(COND, [rec("pass", participants={"ip": NEW, "ew": NEW})], HEADS, "ip", CHECK).state == "config_changed"
    # a partial-set row never outranks admitted evidence
    s = condition_status(XCOND, [rec("pass", cond=XCOND, check=XCHECK, participants={"ip": NEW}),
                                 rec("fail", cond=XCOND, check=XCHECK, participants={"ip": NEW, "ew": NEW})],
                         HEADS, "ip", XCHECK)
    assert s.state == "check_failed"
    # the exact set with one participant behind its head is changed_since, as before
    behind = rec("pass", cond=XCOND, check=XCHECK, participants={"ip": NEW, "ew": OLD})
    assert condition_status(XCOND, [behind], HEADS, "ip", XCHECK).state == "changed_since"


# --- B2 --------------------------------------------------------------------------------------

def _catalogue(tmp_path: Path, checks: dict, repos=("ip",)) -> Path:
    first = next(iter(checks))
    kind = "installed_demo" if checks[first].get("runner") == "manual_observation" else "automated_test"
    cat = {
        "release": {"required_platforms": ["linux"]},
        "repos": {r: {"github": f"x/{r}"} for r in repos},
        "apps": {"app": {"name": "A", "repo": "ip"}},
        "checks": checks,
        "tasks": [{"id": "t", "app": "app", "layer": "standalone", "title": "t", "promise": "p",
                   "conditions": [{"id": "c", "kind": kind, "check": first, "proves": "x"}]}],
    }
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(cat))
    return path


def test_catalogue_rejects_wildcard_and_unlisted_repositories(tmp_path: Path):
    for repo in ("*", "elsewhere", None):
        check = {"runner": "pytest", "platform": "linux"}
        if repo is not None:
            check["repo"] = repo
        with pytest.raises(Exception) as excinfo:
            catmod.load(_catalogue(tmp_path, {"t.pytest": check}))
        assert "repo must name one catalogue repository" in str(excinfo.value)
    loaded = catmod.load(_catalogue(tmp_path, {"t.pytest": {"runner": "pytest", "repo": "ip", "platform": "linux"}}))
    assert "app_repo" not in loaded["tasks"][0]


def test_release_targets_read_the_one_named_repository():
    revisions = {"ew": Revision("ew", "a" * 40, T_OLD, "e"), "ip": Revision("ip", "b" * 40, T_OLD, "i")}
    assert release_targets({"repo": "ip"}, revisions) == [("ip", revisions["ip"])]
    assert release_targets({"repo": "*"}, revisions) == []
    assert release_targets({"repo": "missing"}, revisions) == []


def test_condition_status_requires_a_repository():
    with pytest.raises(ValueError, match="names no repository"):
        condition_status(COND, [rec("pass")], HEADS, "", CHECK)
    # and the filter always applies: a row from another repository is never this condition's evidence
    foreign = rec("pass", repo="ew")
    assert condition_status(COND, [foreign], HEADS, "ip", CHECK).state == "no_evidence"


# --- B3 --------------------------------------------------------------------------------------

def test_old_failure_never_rerun_reads_stale_failure():
    old_fail = rec("fail", revision=OLD, recorded_at="2026-09-01T11:00:00+00:00")
    s = condition_status(COND, [old_fail], HEADS, "ip", CHECK)
    assert s.state == "stale_failure"
    assert s.current is None and s.last_proven is None and s.last_result is old_fail
    assert COND_LABEL["stale_failure"] == "failed at an earlier revision, not re-run"
    payload = condition_payload(s)
    assert payload["label"] == "failed at an earlier revision, not re-run"
    assert payload["last_result"]["record_id"] == old_fail.record_id
    ts = task_status(TASK, [old_fail], HEADS, CAT, CAT["release"])
    assert ts.maturity == "planned"
    assert ts.freshness == "not_checked"
    assert ts.platforms == {"linux": "not_checked"}
    assert next_action(ts) == "Re-run at current code (t.pytest): it works"
    # ranked after changed_since, before config_changed
    other = {"id": "c2", "kind": "automated_test", "check": "t.pytest", "proves": "other"}
    two = {**TASK, "conditions": [other, COND]}
    ts = task_status(two, [rec("pass", cond=other, revision=OLD), old_fail], HEADS, CAT, CAT["release"])
    assert [c.state for c in ts.conditions] == ["changed_since", "stale_failure"]
    assert next_action(ts) == "Re-run at current code (t.pytest): other"
    reconfigured = {"release": CAT["release"], "checks": {"t.pytest": {**CHECK, "args": ["-k", "y"]}}}
    ts = task_status(two, [rec("pass", cond=other, revision=OLD), rec("fail", revision=OLD, check=reconfigured["checks"]["t.pytest"])],
                     HEADS, reconfigured, CAT["release"])
    assert [c.state for c in ts.conditions] == ["config_changed", "stale_failure"]
    assert next_action(ts).startswith("Re-run at current code (t.pytest): it works")
    # the other branches are unchanged
    assert condition_status(COND, [rec("skip", revision=OLD)], HEADS, "ip", CHECK).state == "not_checked"
    later_skip = rec("skip", revision=OLD, recorded_at="2026-09-02T10:00:00+00:00")
    assert condition_status(COND, [old_fail, later_skip], HEADS, "ip", CHECK).state == "not_checked"
    assert condition_status(COND, [old_fail, rec("pass", revision=OLDER)], HEADS, "ip", CHECK).state == "changed_since"
    assert condition_status(COND, [rec("fail")], HEADS, "ip", CHECK).state == "check_failed"


def test_stale_failure_needs_a_known_head():
    old_fail = rec("fail", revision=OLD, recorded_at="2026-09-01T11:00:00+00:00")
    assert condition_status(COND, [old_fail], {}, "ip", CHECK).state == "not_checked"
    assert condition_status(COND, [old_fail], HEADS, "ip", CHECK).state == "stale_failure"
    xfail = rec("fail", cond=XCOND, check=XCHECK, revision=OLD, participants={"ip": OLD, "ew": OLD},
                recorded_at="2026-09-01T11:00:00+00:00")
    assert condition_status(XCOND, [xfail], {"ip": NEW}, "ip", XCHECK).state == "not_checked"
    assert condition_status(XCOND, [xfail], HEADS, "ip", XCHECK).state == "stale_failure"


def test_stale_failure_gate_and_release_fail_closed():
    gate_check = {"runner": "github_issues", "repo": "ip", "milestone": "First Public Release"}
    rel_check = {"runner": "github_release", "repo": "ip"}
    cat = {"release": {"required_platforms": ["linux"]},
           "checks": {"t.pytest": CHECK, "t.gate": gate_check, "t.rel": rel_check}}
    gate = {"id": "g1", "kind": "issue_gate", "check": "t.gate", "proves": "no open first-release issues"}
    rel = {"id": "r1", "kind": "release_artifact", "check": "t.rel", "proves": "a release exists"}
    task = {**TASK, "layer": "release", "conditions": [COND, gate, rel]}

    def old_row(kind, cond, check):
        return Record(kind=kind, repo="ip", revision=OLD, verdict="fail", condition_ids=[cond["id"]],
                      revision_time=T_OLD, recorded_at="2026-09-01T11:00:00+00:00",
                      source={"check": cond["check"], "check_fingerprint": check_fingerprint(check),
                              "condition_fingerprints": {cond["id"]: condition_fingerprint(cond)}})
    ts = task_status(task, [rec("pass"), old_row("issue_gate", gate, gate_check), old_row("release_artifact", rel, rel_check)],
                     HEADS, cat, cat["release"])
    assert [c.state for c in ts.conditions] == ["satisfied", "stale_failure", "stale_failure"]
    assert ts.release_issue_gate == "unavailable" and ts.release_issue_blockers == []
    assert ts.maturity == "built"
    assert next_action(ts) == "Re-run at current code (t.gate): no open first-release issues"


def test_report_and_dashboard_show_the_stale_failure_record():
    old_fail = rec("fail", revision=OLD, recorded_at="2026-09-01T11:00:00+00:00", summary="2 failed, 40 passed")
    condition = ConditionStatus(COND, "stale_failure", last_result=old_fail, platform="linux")
    task = TaskStatus({**TASK, "title": "It works", "promise": "p", "human_involvement": "h"},
                      "planned", "not_checked", [condition], {"linux": "not_checked"}, [])
    catalogue = {"release": {"target": "t", "automate_scope": {"decision": "undecided", "note": "n"}}}
    payload = status_payload(catalogue, [task], HEADS, [], {"runs": 1, "last_run_at": T_NEW}, {}, [old_fail])
    cond = payload["tasks"][0]["conditions"][0]
    assert cond["label"] == "failed at an earlier revision, not re-run"
    assert cond["last_result"]["revision"] == OLD[:12]
    report = report_md(payload, catalogue)
    row = next(line for line in report.splitlines() if "failed at an earlier revision, not re-run" in line)
    assert f"`{OLD[:12]}` fail" in row and "2 failed, 40 passed" in row
    assert "||c.last_result" in dashboard_html(payload, catalogue)


def test_catalogue_validates_declared_participants(tmp_path: Path):
    base = {"runner": "manual_observation", "repo": "ip", "platform": "linux"}
    for bad in ([], "ip", ["ip", "ip"], ["ip", "zz"], ["ew"]):
        with pytest.raises(Exception) as excinfo:
            catmod.load(_catalogue(tmp_path, {"t.demo": {**base, "participants": bad}}, repos=("ip", "ew")))
        assert "participants must be a non-empty list" in str(excinfo.value), bad
    loaded = catmod.load(_catalogue(tmp_path, {"t.demo": {**base, "participants": ["ip", "ew"]}}, repos=("ip", "ew")))
    assert loaded["checks"]["t.demo"]["participants"] == ["ip", "ew"]


# --- B4 --------------------------------------------------------------------------------------

def test_store_rejects_non_aware_instants_at_write(tmp_path: Path):
    path = tmp_path / "records.jsonl"
    store = Store(path)
    good = rec("pass", recorded_at="2026-09-09T12:00:00+02:00")
    assert store.add(good) is True
    before = path.read_bytes()
    for bad in (
        rec("pass", recorded_at="2026-09-09T12:00:00"),                 # naive recorded_at
        rec("pass", revision_time="2026-09-09T10:00:00"),               # naive revision_time
        rec("pass", recorded_at="yesterday"),                            # unparseable
        rec("pass", revision_time=""),                                   # present and empty
    ):
        with pytest.raises(ValueError, match="aware ISO-8601 instant"):
            store.add(bad)
        assert path.read_bytes() == before and len(store) == 1
    later = rec("fail", recorded_at="2026-09-09T11:00:00Z")            # 11:00 UTC is after 12:00+02:00 (10:00 UTC)
    assert instant_key(later.recorded_at) > instant_key(good.recorded_at)
    # and the rules order by the absolute instant, not the text: the Z row is the current one
    assert condition_status(COND, [good, later], HEADS, "ip", CHECK).current is later


def test_collector_records_a_rejected_row_as_a_source_failure(tmp_path: Path):
    store = Store(tmp_path / "records.jsonl")
    failures: list = []
    log = tmp_path / "logs" / "run.log"
    log.parent.mkdir()
    log.write_text("1 failed\n")
    bad = rec("fail", recorded_at="2026-09-09T12:00:00", log_path=str(log))
    store_result(store, failures, bad)
    rows = store.all()
    assert [r.kind for r in rows] == ["collection_failure"]
    assert rows[0].verdict == "unavailable" and rows[0].repo == "ip" and rows[0].revision_time is None
    assert "recorded_at is not an aware ISO-8601 instant" in rows[0].summary
    assert rows[0].log_path == str(log) and log.exists()          # the run's own artifact survives
    assert failures == [{"repo": "ip", "what": "automated_test",
                         "why": rows[0].summary}]
    assert condition_status(COND, rows, HEADS, "ip", CHECK).state == "no_evidence"


def test_collector_guards_every_store_write(tmp_path: Path):
    source = Path(__file__).resolve().parent.parent / "lcstatus" / "collect.py"
    body = source.read_text().split("def main(", 1)[1]
    assert "store.add(" not in body                                # every write goes through add_record
    store = Store(tmp_path / "records.jsonl")
    failures: list = []
    revision_row = Record(kind="revision", repo="ip", revision=NEW, revision_time="2026-09-09T10:00:00",
                          verdict="pass", summary="head")
    assert add_record(store, failures, revision_row) is None
    rows = store.all()
    assert [r.kind for r in rows] == ["collection_failure"] and rows[0].revision == NEW
    assert failures[0]["what"] == "revision" and "revision_time" in failures[0]["why"]
    # an ordinary row still stores, and the collapse answer still comes back
    assert add_record(store, failures, rec("pass")) is True
    assert add_record(store, failures, rec("pass")) is False


def test_every_snapshot_row_passes_write_time_validation():
    """The repository's checked-in store only; the live-store proof is run before merge."""
    root = Path(__file__).resolve().parent.parent
    rows = [Record.from_dict(json.loads(l)) for l in (root / "data" / "records.jsonl").read_text().splitlines() if l.strip()]
    assert rows
    for r in rows:
        validate_record_instants(r)


# --- I2 --------------------------------------------------------------------------------------

def test_snapshot_admission_is_unchanged_by_participant_completeness():
    """Known limit: the checked-in snapshot is the legacy prefix; the single-app demo shape is
    covered synthetically above and by the live-store proof before merge."""
    root = Path(__file__).resolve().parent.parent
    catalogue = catmod.load(root / "catalogue.json")
    records = Store(root / "data" / "records.jsonl").all()
    checks = catalogue["checks"]
    partial = 0
    for task in catalogue["tasks"]:
        for cond in task["conditions"]:
            chk = checks[cond["check"]]
            declared = set(chk["participants"]) if "participants" in chk else {chk["repo"]}
            same = [r for r in records if cond["id"] in r.condition_ids and r.source.get("check") == cond["check"]]
            before = {r.record_id for r in same
                      if resolve_check_fingerprint(record_check_fingerprint(r)) == check_fingerprint(chk)
                      and record_condition_fingerprint(r, cond["id"]) == condition_fingerprint(cond)}
            after = {r.record_id for r in same
                     if resolve_check_fingerprint(record_check_fingerprint(r)) == check_fingerprint(chk)
                     and record_condition_fingerprint(r, cond["id"]) == condition_fingerprint(cond)
                     and (set(r.participants) == declared or (not r.participants and "participants" not in chk))}
            assert before == after, cond["id"]
            partial += sum(1 for r in same if "participants" in chk and set(r.participants) != declared)
    assert partial >= 1   # the two-participant-era rows exist and are excluded under both rules
