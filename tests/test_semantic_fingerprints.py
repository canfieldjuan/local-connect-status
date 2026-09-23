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


def _real_noted_check():
    root = Path(__file__).resolve().parent.parent
    catalogue = json.loads((root / "catalogue.json").read_text())
    conditions = {c["id"]: c for t in catalogue["tasks"] for c in t["conditions"]}
    condition = next(
        c for c in conditions.values()
        if c["kind"] == "automated_test" and catalogue["checks"][c["check"]].get("note")
        and catalogue["checks"][c["check"]].get("repo") == "invoice-processor"
        and c["check"] != "xapp.accept_ew_to_ip"
    )
    return catalogue, condition, catalogue["checks"][condition["check"]]


def _real_rec(condition, check, verdict, fingerprint, revision=NEW):
    return Record(
        kind="automated_test", repo=check["repo"], revision=revision, verdict=verdict,
        platform=check.get("platform", "n/a"), condition_ids=[condition["id"]],
        revision_time="2026-09-09T10:00:00+00:00" if revision == NEW else "2026-09-01T10:00:00+00:00",
        source={"check": condition["check"], "check_fingerprint": fingerprint,
                "condition_fingerprints": {condition["id"]: condition_fingerprint(condition)}},
    )


def test_note_edit_keeps_whole_dict_and_semantic_evidence_admitted():
    _, condition, check = _real_noted_check()
    shipped_whole = whole_check_fingerprint(check)
    assert shipped_whole in CHECK_FINGERPRINT_ALIASES            # the frozen table, as shipped
    assert CHECK_FINGERPRINT_ALIASES[shipped_whole] == check_fingerprint(check)
    edited = {**check, "note": "rewritten after the mechanism changed"}
    assert check_fingerprint(edited) == check_fingerprint(check)
    assert whole_check_fingerprint(edited) != shipped_whole
    whole = _real_rec(condition, check, "pass", shipped_whole)
    semantic = _real_rec(condition, check, "pass", check_fingerprint(check))
    heads = {check["repo"]: NEW}
    assert condition_status(condition, [whole], heads, check["repo"], edited).state == "satisfied"
    assert condition_status(condition, [semantic], heads, check["repo"], edited).state == "satisfied"


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
    hexes = re.compile(r"^[0-9a-f]{24}$")
    for key, value in CHECK_FINGERPRINT_ALIASES.items():
        assert hexes.match(key) and hexes.match(value)
        assert resolve_check_fingerprint(key) == value
    assert resolve_check_fingerprint("0" * 24) == "0" * 24
    assert resolve_check_fingerprint(None) is None
    assert resolve_check_fingerprint(["not", "a", "fingerprint"]) == ["not", "a", "fingerprint"]
    assert resolve_check_fingerprint({"x": 1}) == {"x": 1}
    assert sum(1 for k, v in CHECK_FINGERPRINT_ALIASES.items() if k == v) == 16


def test_frozen_alias_table_covers_every_check_in_the_catalogue():
    root = Path(__file__).resolve().parent.parent
    catalogue = json.loads((root / "catalogue.json").read_text())
    edited_by_b8 = {"xapp.accept_ew_to_ip"}
    reconfigured = {"ew.pytest.entitlement"}
    values = set(CHECK_FINGERPRINT_ALIASES.values())
    for check_id, check in catalogue["checks"].items():
        semantic = check_fingerprint(check)
        if check_id in reconfigured:
            old = {**check, "args": ["tests/test_entitlement.py"]}
            assert check_fingerprint(old) in values
            assert semantic not in values
            assert whole_check_fingerprint(check) not in CHECK_FINGERPRINT_ALIASES
            continue
        assert semantic in values, check_id
        if check_id in edited_by_b8:
            # its whole-dict value moved with the note; the frozen key is the pre-edit one
            assert whole_check_fingerprint(check) not in CHECK_FINGERPRINT_ALIASES
        else:
            assert CHECK_FINGERPRINT_ALIASES[whole_check_fingerprint(check)] == semantic, check_id


def test_malformed_fingerprint_row_is_ignored_without_raising():
    bad = rec("pass", fingerprint=None)
    bad.source["check_fingerprint"] = ["not", "a", "fingerprint"]
    assert status([bad]).state != "satisfied"
    assert status([bad], check={**CHECK, "args": ["-k", "y"]}).state != "satisfied"
    good = rec("pass")
    assert status([bad, good]).state == "satisfied"


def test_config_changed_issue_gate_fails_closed_with_the_right_next_action():
    gate_check = {"runner": "github_issues", "repo": "ip", "milestone": "First Public Release"}
    former_gate = {**gate_check, "milestone": "Beta"}
    cat = {"release": {"required_platforms": ["linux"]}, "checks": {"t.pytest": CHECK, "t.gate": gate_check}}
    gate = {"id": "g1", "kind": "issue_gate", "check": "t.gate", "proves": "no open first-release issues"}
    task = {**TASK, "layer": "release", "conditions": [COND, gate]}
    old_clear = Record(kind="issue_gate", repo="ip", revision=OLD, verdict="pass", condition_ids=["g1"],
                       revision_time="2026-09-01T10:00:00+00:00",
                       source={"check": "t.gate", "check_fingerprint": check_fingerprint(former_gate),
                               "condition_fingerprints": {"g1": condition_fingerprint(gate)}})
    ts = task_status(task, [rec("pass"), old_clear], HEADS, cat, cat["release"])
    assert [c.state for c in ts.conditions] == ["satisfied", "config_changed"]
    assert ts.release_issue_gate == "unavailable"
    assert ts.maturity == "built"          # never "ready for release"
    assert next_action(ts) == "Re-verify under the current configuration (t.gate): no open first-release issues"
    # a gate with no readable result at all still asks for visibility
    ts = task_status(task, [rec("pass")], HEADS, cat, cat["release"])
    assert ts.conditions[1].state == "no_evidence"
    assert next_action(ts) == "Restore GitHub issue visibility for: ip"


def test_config_changed_inherits_the_historical_platform():
    bare_check = {"runner": "pytest", "repo": "ip", "args": ["-k", "x"]}
    cat = {"release": {"required_platforms": ["linux"]}, "checks": {"t.pytest": {**bare_check, "args": ["-k", "y"]}}}
    old_pass = rec("pass", revision=OLD, check=bare_check, platform="linux")
    ts = task_status(TASK, [old_pass], HEADS, cat, cat["release"])
    assert ts.conditions[0].state == "config_changed"
    assert ts.conditions[0].platform == "linux"
    assert ts.platforms == {"linux": "changed_since"}


def test_semantic_redelivery_of_an_unchanged_result_collapses_onto_its_whole_dict_row(tmp_path: Path):
    root = Path(__file__).resolve().parent.parent
    stored = (root / "data" / "records.jsonl").read_bytes()
    prefix = b"".join(stored.splitlines(keepends=True)[:LEGACY_EVIDENCE_PREFIX_ROWS])
    _, condition, check = _real_noted_check()
    whole = _real_rec(condition, check, "pass", whole_check_fingerprint(check))
    path = tmp_path / "records.jsonl"
    path.write_bytes(prefix + whole.to_json().encode() + b"\n")
    store = Store(path)
    before_bytes = path.read_bytes()
    before_len = len(store)
    again = _real_rec(condition, check, "pass", check_fingerprint(check))
    assert again.record_id != whole.record_id            # ids never move
    assert store.add(again) is False                     # same observation, new spelling
    assert path.read_bytes() == before_bytes and len(store) == before_len
    changed = _real_rec(condition, check, "fail", check_fingerprint(check))
    assert store.add(changed) is True                    # a changed result is still appended
    assert len(store) == before_len + 1
    # and the ordinary rule is intact: the same semantic delivery twice is stored once
    assert store.add(_real_rec(condition, check, "fail", check_fingerprint(check))) is False


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
