"""Contract 04: the rules say only what the evidence says."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lcstatus import catalogue as catmod
from lcstatus.collect import release_targets, store_result
from lcstatus.evidence import (
    Record, Store, check_fingerprint, condition_fingerprint, instant_key, record_check_fingerprint,
    record_condition_fingerprint, resolve_check_fingerprint, validate_record_instants,
)
from lcstatus.render import COND_LABEL, condition_payload, next_action
from lcstatus.rules import condition_status, task_status
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

def _catalogue(tmp_path: Path, checks: dict) -> Path:
    cat = {
        "release": {"required_platforms": ["linux"]},
        "repos": {"ip": {"github": "x/ip"}},
        "apps": {"app": {"name": "A", "repo": "ip"}},
        "checks": checks,
        "tasks": [{"id": "t", "app": "app", "layer": "standalone", "title": "t", "promise": "p",
                   "conditions": [{"id": "c", "kind": "automated_test", "check": next(iter(checks)), "proves": "x"}]}],
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
    later = rec("pass", recorded_at="2026-09-09T11:00:00Z")            # 11:00 UTC is after 12:00+02:00 (10:00 UTC)
    assert instant_key(later.recorded_at) > instant_key(good.recorded_at)
    earlier = rec("pass", recorded_at="2026-09-09T09:30:00Z")
    assert instant_key(earlier.recorded_at) < instant_key(good.recorded_at)


def test_collector_records_a_rejected_row_as_a_source_failure(tmp_path: Path):
    store = Store(tmp_path / "records.jsonl")
    failures: list = []
    bad = rec("fail", recorded_at="2026-09-09T12:00:00")
    store_result(store, failures, bad)
    rows = store.all()
    assert [r.kind for r in rows] == ["collection_failure"]
    assert rows[0].verdict == "unavailable" and rows[0].repo == "ip" and rows[0].revision_time is None
    assert "recorded_at is not an aware ISO-8601 instant" in rows[0].summary
    assert failures == [{"repo": "ip", "what": "automated_test",
                         "why": rows[0].summary}]
    assert condition_status(COND, rows, HEADS, "ip", CHECK).state == "no_evidence"


def test_every_live_row_passes_write_time_validation():
    root = Path(__file__).resolve().parent.parent
    rows = [Record.from_dict(json.loads(l)) for l in (root / "data" / "records.jsonl").read_text().splitlines() if l.strip()]
    assert rows
    for r in rows:
        validate_record_instants(r)


# --- I2 --------------------------------------------------------------------------------------

def test_admitted_sets_are_identical_before_and_after_participant_completeness():
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
