"""Contract 03: prose is not configuration; a changed configuration stays visible."""

from __future__ import annotations

import json
import re
from pathlib import Path

from lcstatus.evidence import (
    CHECK_FINGERPRINT_ALIASES, CHECK_PROSE_KEYS, LEGACY_EVIDENCE_PREFIX_ROWS, Record, Store,
    check_fingerprint, condition_fingerprint, record_check_fingerprint,
    record_condition_fingerprint, resolve_check_fingerprint, whole_check_fingerprint,
)
from lcstatus.render import COND_LABEL, condition_payload, next_action
from lcstatus.rules import condition_status, task_status

NEW, OLD = "b" * 40, "a" * 40
CHECK = {"runner": "pytest", "repo": "ip", "platform": "linux", "args": ["-k", "x"], "note": "why"}
CAT = {"release": {"required_platforms": ["linux"]}, "checks": {"t.pytest": CHECK}}
COND = {"id": "c1", "kind": "automated_test", "check": "t.pytest", "proves": "it works"}
TASK = {"id": "t", "layer": "connect", "app": "x", "app_repo": "ip", "conditions": [COND], "depends_on": []}
HEADS = {"ip": NEW}


def rec(verdict, *, check=CHECK, cond=COND, revision=NEW, fingerprint=None, **kw):
    kw.setdefault("repo", "ip")
    kw.setdefault("platform", "linux")
    kw.setdefault("revision_time", "2026-09-09T10:00:00+00:00" if revision == NEW else "2026-09-01T10:00:00+00:00")
    source = {"check": "t.pytest", "condition_fingerprints": {cond["id"]: condition_fingerprint(cond)}}
    if fingerprint is not False:
        source["check_fingerprint"] = fingerprint or check_fingerprint(check)
    return Record(kind="automated_test", revision=revision, verdict=verdict, condition_ids=[cond["id"]],
                  source=source, **kw)


def status(records, *, check=CHECK, cond=COND, heads=HEADS):
    return condition_status(cond, records, heads, "ip", check)


def test_note_edit_keeps_whole_dict_and_semantic_evidence_admitted():
    edited = {**CHECK, "note": "rewritten after slice 1"}
    assert check_fingerprint(edited) == check_fingerprint(CHECK)
    assert whole_check_fingerprint(edited) != whole_check_fingerprint(CHECK)
    whole = rec("pass", fingerprint=whole_check_fingerprint(CHECK))
    semantic = rec("pass")
    aliases = dict(CHECK_FINGERPRINT_ALIASES)
    aliases[whole_check_fingerprint(CHECK)] = check_fingerprint(CHECK)
    try:
        CHECK_FINGERPRINT_ALIASES.update(aliases)
        assert status([whole], check=edited).state == "satisfied"
        assert status([semantic], check=edited).state == "satisfied"
    finally:
        CHECK_FINGERPRINT_ALIASES.pop(whole_check_fingerprint(CHECK), None)


def test_semantic_edit_still_invalidates_and_stays_visible():
    old_pass = rec("pass", revision=OLD)
    reconfigured = {**CHECK, "args": ["-k", "y"]}
    s = status([old_pass], check=reconfigured)
    assert s.state == "config_changed"
    assert s.last_proven is old_pass and s.current is None
    reclaimed = {**COND, "proves": "it works differently"}
    s = status([old_pass], cond=reclaimed)
    assert s.state == "config_changed" and s.last_proven is old_pass
    new_pass = rec("pass", check=reconfigured)
    assert status([old_pass, new_pass], check=reconfigured).state == "satisfied"


def test_config_changed_is_visible_but_never_proving():
    cat = {"release": {"required_platforms": ["linux"]}, "checks": {"t.pytest": {**CHECK, "args": ["-k", "y"]}}}
    old_pass = rec("pass", revision=OLD)
    ts = task_status(TASK, [old_pass], HEADS, cat, cat["release"])
    c = ts.conditions[0]
    assert c.state == "config_changed"
    assert ts.maturity == "planned"
    assert ts.freshness == "changed_since_verification"
    assert ts.platforms == {"linux": "changed_since"}
    assert COND_LABEL["config_changed"] == "configuration changed since verification"
    assert condition_payload(c)["label"] == "configuration changed since verification"
    assert next_action(ts) == "Re-verify under the current configuration (t.pytest): it works"
    release_task = {**TASK, "layer": "release"}
    rts = task_status(release_task, [old_pass], HEADS, cat, cat["release"])
    assert rts.maturity == "planned"


def test_config_changed_ranks_between_changed_since_and_not_checked():
    other = {"id": "c2", "kind": "automated_test", "check": "t.pytest", "proves": "other"}
    task = {**TASK, "conditions": [COND, other]}
    cat = {"release": {"required_platforms": ["linux"]}, "checks": {"t.pytest": {**CHECK, "args": ["-k", "y"]}}}
    stale = rec("pass", revision=OLD, check=cat["checks"]["t.pytest"])
    superseded = rec("pass", revision=OLD, cond=other)
    ts = task_status(task, [stale, superseded], HEADS, cat, cat["release"])
    assert [c.state for c in ts.conditions] == ["changed_since", "config_changed"]
    assert next_action(ts).startswith("Re-run at current code")
    superseded_c1 = rec("pass", revision=OLD)
    skipped = rec("skip", cond=other, check=cat["checks"]["t.pytest"])
    ts = task_status(task, [superseded_c1, skipped], HEADS, cat, cat["release"])
    assert [c.state for c in ts.conditions] == ["config_changed", "not_checked"]
    assert next_action(ts).startswith("Re-verify under the current configuration")


def test_admitted_evidence_takes_precedence_over_configuration_history():
    reconfigured = {**CHECK, "args": ["-k", "y"]}
    old_pass = rec("pass", revision=OLD)
    current_fail = rec("fail", check=reconfigured)
    s = status([old_pass, current_fail], check=reconfigured)
    assert s.state == "check_failed" and s.last_proven is None
    old_skip = rec("skip", revision=OLD, check=reconfigured)
    assert status([old_pass, old_skip], check=reconfigured).state == "not_checked"


def test_fingerprint_free_rows_outside_the_prefix_stay_excluded():
    bare = rec("pass", revision=OLD, fingerprint=False)
    assert record_check_fingerprint(bare) is None
    assert status([bare]).state == "no_evidence"
    assert status([bare], check={**CHECK, "args": ["-k", "y"]}).state == "no_evidence"


def test_alias_table_resolves_only_known_whole_dict_fingerprints():
    assert CHECK_PROSE_KEYS == ("note",)
    assert len(CHECK_FINGERPRINT_ALIASES) == 44
    assert len(set(CHECK_FINGERPRINT_ALIASES)) == 44
    hexes = re.compile(r"^[0-9a-f]{24}$")
    for key, value in CHECK_FINGERPRINT_ALIASES.items():
        assert hexes.match(key) and hexes.match(value)
        assert resolve_check_fingerprint(key) == value
    assert resolve_check_fingerprint("0" * 24) == "0" * 24
    assert resolve_check_fingerprint(None) is None
    assert sum(1 for k, v in CHECK_FINGERPRINT_ALIASES.items() if k == v) == 16


def test_frozen_alias_table_covers_every_check_in_the_catalogue():
    root = Path(__file__).resolve().parent.parent
    catalogue = json.loads((root / "catalogue.json").read_text())
    for check in catalogue["checks"].values():
        semantic = check_fingerprint(check)
        assert CHECK_FINGERPRINT_ALIASES.get(whole_check_fingerprint(check), semantic) == semantic
        assert semantic in CHECK_FINGERPRINT_ALIASES.values()


def _legacy_admission(record, cond, check):
    return (
        record_check_fingerprint(record) == whole_check_fingerprint(check)
        and record_condition_fingerprint(record, cond["id"]) == condition_fingerprint(cond)
    )


def test_admitted_sets_are_identical_before_and_after(tmp_path: Path):
    root = Path(__file__).resolve().parent.parent
    catalogue = json.loads((root / "catalogue.json").read_text())
    stored = (root / "data" / "records.jsonl").read_bytes()
    prefix = b"".join(stored.splitlines(keepends=True)[:LEGACY_EVIDENCE_PREFIX_ROWS])
    checks = catalogue["checks"]
    conditions = {c["id"]: c for t in catalogue["tasks"] for c in t["conditions"]}
    condition = next(
        c for c in conditions.values()
        if c["kind"] == "automated_test" and checks[c["check"]].get("note")
        and checks[c["check"]].get("repo") == "invoice-processor"
    )
    check = checks[condition["check"]]
    assert whole_check_fingerprint(check) != check_fingerprint(check)   # the note is in play
    whole = Record(kind="automated_test", repo="invoice-processor", revision=OLD, verdict="pass",
                   condition_ids=[condition["id"]],
                   source={"check": condition["check"], "check_fingerprint": whole_check_fingerprint(check),
                           "condition_fingerprints": {condition["id"]: condition_fingerprint(condition)}})
    semantic = Record(kind="automated_test", repo="invoice-processor", revision=OLD, verdict="pass",
                      condition_ids=[condition["id"]],
                      source={"check": condition["check"], "check_fingerprint": check_fingerprint(check),
                              "condition_fingerprints": {condition["id"]: condition_fingerprint(condition)}})
    bare = Record(kind="automated_test", repo="invoice-processor", revision=OLD, verdict="pass",
                  condition_ids=[condition["id"]], source={"check": condition["check"]})
    mixed = tmp_path / "records.jsonl"
    mixed.write_bytes(prefix + b"".join(r.to_json().encode() + b"\n" for r in (whole, semantic, bare)))
    records = Store(mixed).all()
    ids = {r.record_id for r in records}
    assert {whole.record_id, semantic.record_id, bare.record_id} <= ids
    assert record_check_fingerprint(records[0]) is not None   # prefix authenticated
    before = {}
    after = {}
    for cid, cond in conditions.items():
        chk = checks[cond["check"]]
        same = [r for r in records if cid in r.condition_ids and r.source.get("check") == cond["check"]]
        before[cid] = {r.record_id for r in same if _legacy_admission(r, cond, chk)}
        after[cid] = {
            r.record_id for r in same
            if resolve_check_fingerprint(record_check_fingerprint(r)) == check_fingerprint(chk)
            and record_condition_fingerprint(r, cid) == condition_fingerprint(cond)
        }
        assert after[cid] >= before[cid], cid
    # the only difference the new rule may introduce: a row written with the semantic value
    assert after[condition["id"]] - before[condition["id"]] == {semantic.record_id}
    assert bare.record_id not in after[condition["id"]]
    assert whole.record_id in before[condition["id"]] and whole.record_id in after[condition["id"]]
