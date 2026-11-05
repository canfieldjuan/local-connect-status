"""Accepted Contract 10 gap proof: fixture mirrors, packages and stores only."""
import copy
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from lcstatus import install_gap
from lcstatus.evidence import Record, Store, check_fingerprint, collection_lock
from lcstatus.promotion import installed_programs, package_identity
from lcstatus.rules import matching_promotion, validate_promotion_member
from scripts import record_observation as recorder
from test_promotion import fixture, args, gates_at, git, package, debian_package, rows, NOW


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def gap_case(fixture, request):
    f = fixture
    assert recorder.record_promotion(args(f), f.cat, "2026-10-10T12:03:00+00:00",
                                     f.data, f.mirrors_root) == 0
    f.predecessor = rows(f)[-1]
    events = []
    for index, day in enumerate([11, 12, 13]):
        if day == 13 and getattr(request, "param", None) == "docs-only":
            (f.work / "README.md").write_text("documentation only")
        else:
            (f.work / "src/app.py").write_text(str(day))
        git(f.work, "add", ".")
        git(f.work, "commit", "-m", str(day))
        revision = git(f.work, "rev-parse", "HEAD")
        content = ("installed-" + str(day)).encode()
        f.install.write_bytes(content)
        pkg = package(f.root / f"package-{day}.tar", {str(f.install): content})
        if day == 13:
            f.revision, f.pkg = revision, pkg
            break
        event = {"revision": revision, "installed_at": f"2026-10-{day}T12:00:00+00:00",
                 "package_sha256": sha(pkg),
                 "program_hashes": {str(f.install): hashlib.sha256(content).hexdigest()}}
        receipt = f.root / f"installed-{day}.json"
        receipt.write_text(json.dumps({"version": 1, "app": "fixture", "repo": "fixture",
                                      "platform": "linux", "installed": True, **event}))
        events.append({**event, "package": str(pkg), "installed_observation_record_ids": [], "receipts": [
            {"format": "installed-v1", "path": str(receipt), "sha256": sha(receipt)}]})
    git(f.mirrors.path("fixture"), "fetch", "origin")
    f.gate_ids = gates_at(f, f.revision)
    f.previous_package = events[-1]["package"]
    f.payload = {"version": 2, "app": "fixture", "repo": "fixture", "platform": "linux",
                 "candidate_revision": f.revision,
                 "last_recorded_promotion_id": f.predecessor.record_id,
                 "unrecorded_installs": events, "history_attested_complete": True}
    f.gap_file = f.root / "gap.json"
    f.artifact = f.root / "human-review.md"
    f.artifact.write_text("Synthetic human-reviewed installed-history evidence.")
    return f


def record_gap(f, **changes):
    f.gap_file.write_text(json.dumps(f.payload))
    events = f.payload["unrecorded_installs"]
    previous = events[-1].get("package", f.previous_package) if events else f.previous_package
    options = {"install_gap": ["fixture=" + str(f.gap_file)], "gap_record": [],
               "gate_record": f.gate_ids, "artifact": str(f.artifact),
               "rollback_package": ["fixture=" + previous]}
    return recorder.record_promotion(args(f, **(options | changes)), f.cat, NOW,
                                     f.data, f.mirrors_root)


def test_gap_records_real_previous_package(gap_case):
    f = gap_case
    assert record_gap(f) == 0
    retained = rows(f)
    gap, promotion = retained[-2:]
    assert gap.kind == "installed_demo" and gap.source["type"] == "promotion_gap"
    assert gap.verdict == "pass" and gap.condition_ids == [] and "check" not in gap.source
    assert gap.source["install_gap"] == f.payload
    fallback = promotion.detail["members"]["fixture"]["fallback"]
    assert fallback["previous_record"] == f.predecessor.record_id
    assert fallback["previous_install_revision"] == f.payload["unrecorded_installs"][-1]["revision"]
    assert fallback["install_gap_record_id"] == gap.record_id
    assert fallback["sha256"] == f.payload["unrecorded_installs"][-1]["package_sha256"]


def assert_refused(f, **changes):
    before = (f.data / "records.jsonl").read_bytes()
    assert record_gap(f, **changes) == 2
    assert (f.data / "records.jsonl").read_bytes() == before


def test_recorder_rejects_omitted_collector_install(gap_case):
    f = gap_case
    first = f.payload["unrecorded_installs"][0]
    Store(f.data / "records.jsonl").add(Record(
        kind="installed_observation", repo="fixture", revision="", verdict="pass", platform="linux",
        source={"type": "installed_program_hashes", "app": "fixture"},
        detail={"app": "fixture", "installed": {"hashes": first["program_hashes"], "missing": [], "errors": {}}},
        recorded_at=first["installed_at"]))
    f.payload["unrecorded_installs"] = f.payload["unrecorded_installs"][1:]
    assert_refused(f)


def test_witnessed_event_cannot_claim_to_be_unobserved(gap_case):
    f = gap_case
    event = f.payload["unrecorded_installs"][-1]
    Store(f.data / "records.jsonl").add(Record(
        kind="installed_observation", repo="fixture", revision="", verdict="pass", platform="linux",
        source={"type": "installed_program_hashes", "app": "fixture"},
        detail={"app": "fixture", "installed": {"hashes": event["program_hashes"], "missing": [], "errors": {}}},
        recorded_at=event["installed_at"]))
    assert_refused(f)


@pytest.mark.parametrize("damage", ["legacy-version", "bool-version", "zero-version", "string-version", "incomplete", "empty",
    "extra", "missing", "programs", "short-digest", "order", "duplicate", "platform", "candidate", "predecessor", "no-fallback"])
def test_closed_gap_refuses_before_append(gap_case, damage):
    f = gap_case; p = f.payload
    if damage == "legacy-version": p["version"] = 1
    elif damage == "bool-version": p["version"] = True
    elif damage == "zero-version": p["version"] = 0
    elif damage == "string-version": p["version"] = "1"
    elif damage == "incomplete": p["history_attested_complete"] = False
    elif damage == "empty": p["unrecorded_installs"] = []
    elif damage == "extra": p["waive_gates"] = True
    elif damage == "missing": del p["repo"]
    elif damage == "programs": p["unrecorded_installs"][0]["program_hashes"] = {}
    elif damage == "short-digest": p["unrecorded_installs"][0]["package_sha256"] = "a" * 63
    elif damage == "order": p["unrecorded_installs"].reverse()
    elif damage == "duplicate": p["unrecorded_installs"][1]["receipts"] = p["unrecorded_installs"][0]["receipts"]
    elif damage == "platform": p["platform"] = "windows"
    elif damage == "candidate": p["candidate_revision"] = "b" * 40
    elif damage == "predecessor": p["last_recorded_promotion_id"] = "b" * 24
    elif damage == "no-fallback": del p["unrecorded_installs"][-1]["package"]
    assert_refused(f, rollback_package=[])


@pytest.mark.parametrize("damage", ["changed-bytes", "installed", "source", "time", "program", "extra", "builder-only"])
def test_installed_receipt_proof_is_required(gap_case, damage):
    f = gap_case; ref = f.payload["unrecorded_installs"][-1]["receipts"][0]
    path = Path(ref["path"]); receipt = json.loads(path.read_text())
    if damage == "changed-bytes": receipt["installed"] = False
    elif damage == "installed": receipt["installed"] = False
    elif damage == "source": receipt["revision"] = "b" * 40
    elif damage == "time": receipt["installed_at"] = "2026-10-01T12:00:00Z"
    elif damage == "program": receipt["program_hashes"][str(f.install)] = "b" * 64
    elif damage == "extra": receipt["check"] = "smoke"
    elif damage == "builder-only": del receipt["installed"]
    path.write_text(json.dumps(receipt))
    if damage != "changed-bytes": ref["sha256"] = sha(path)
    assert_refused(f)


def test_valid_single_event_and_reinstall_history(gap_case):
    f = gap_case
    f.payload["unrecorded_installs"] = f.payload["unrecorded_installs"][-1:]
    assert record_gap(f) == 0


def test_repeated_revision_is_not_a_duplicate_install(gap_case):
    f = gap_case; first, last = f.payload["unrecorded_installs"]
    last["revision"] = first["revision"]
    ref = last["receipts"][0]; path = Path(ref["path"])
    receipt = json.loads(path.read_text());receipt["revision"] = last["revision"]
    path.write_text(json.dumps(receipt));ref["sha256"] = sha(path)
    assert record_gap(f) == 0


def test_older_recorded_package_cannot_replace_real_fallback(gap_case):
    f = gap_case
    assert_refused(f, rollback_package=["fixture=" + f.predecessor.detail["members"]["fixture"]["package"]])


def test_payload_programs_must_match_installed_receipt_not_just_package_hash(gap_case):
    f = gap_case; event = f.payload["unrecorded_installs"][-1]
    bad = package(f.root / "different.tar", {str(f.install): b"wrong program"})
    event["package"], event["package_sha256"] = str(bad), sha(bad)
    ref = event["receipts"][0]; path = Path(ref["path"])
    witness = json.loads(path.read_text());witness["package_sha256"] = event["package_sha256"]
    path.write_text(json.dumps(witness));ref["sha256"] = sha(path)
    assert_refused(f)


def test_gap_retry_is_idempotent(gap_case):
    f = gap_case; assert record_gap(f) == 0
    gap = rows(f)[-2]; before = (f.data / "records.jsonl").read_bytes()
    assert record_gap(f, install_gap=[], gap_record=["fixture=" + gap.record_id]) == 0
    assert (f.data / "records.jsonl").read_bytes() == before


def test_new_recorded_predecessor_refuses_stale_gap(gap_case):
    f = gap_case
    newer = replace(f.predecessor, source={"observed_by": "another fixture human"},
                    recorded_at="2026-10-10T12:04:00Z", record_id="")
    Store(f.data / "records.jsonl").add(newer)
    assert_refused(f)


@pytest.mark.parametrize("update", ["promotion", "collector"])
def test_waiting_recorder_rereads_predecessor_after_real_lock(gap_case, update):
    from test_record_observation import read_line
    f = gap_case
    omitted = f.payload["unrecorded_installs"][0]
    if update == "collector": f.payload["unrecorded_installs"] = f.payload["unrecorded_installs"][1:]
    f.gap_file.write_text(json.dumps(f.payload))
    catalogue = f.root / "catalogue.json";catalogue.write_text(json.dumps(f.cat))
    command = [sys.executable, str(Path(recorder.__file__)), "--kind", "promotion", "--app", "fixture",
               "--participant", "fixture=" + f.revision, "--package", "fixture=" + str(f.pkg),
               "--install-gap", "fixture=" + str(f.gap_file), "--verdict", "pass", "--platform", "linux",
               "--artifact", str(f.artifact), "--summary", "fixture", "--observed-at", NOW,
               "--observed-by", "Fixture human", "--data", str(f.data), "--mirrors", str(f.mirrors_root),
               "--catalogue", str(catalogue)]
    for value in f.gate_ids: command += ["--gate-record", value]
    held = collection_lock(f.data, wait=False)
    child = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert "waiting for the collection lock" in read_line(child.stderr)
        newer = replace(f.predecessor, source={"observed_by": "concurrent fixture human"},
                        recorded_at="2026-10-10T12:04:00Z", record_id="")
        if update == "collector":
            newer = Record(kind="installed_observation", repo="fixture", revision="", verdict="pass", platform="linux",
                source={"type": "installed_program_hashes", "app": "fixture"},
                detail={"app": "fixture", "installed": {"hashes": omitted["program_hashes"], "missing": [], "errors": {}}},
                recorded_at=omitted["installed_at"])
        Store(f.data / "records.jsonl").add(newer)
        before = (f.data / "records.jsonl").read_bytes()
        held.close()
        out, err = child.communicate(timeout=20)
        assert child.returncode == 2 and ("stale" if update == "promotion" else "observed") in err, (out, err)
        assert (f.data / "records.jsonl").read_bytes() == before
    finally:
        held.close()
        if child.poll() is None: child.kill()
        child.wait(timeout=10)


def test_orphan_gap_retry_and_stale_predecessor(gap_case, monkeypatch):
    f = gap_case; real = Store.add
    def fail_promotion(self, record):
        if record.kind == "promotion": raise OSError("fixture append failure")
        return real(self, record)
    with monkeypatch.context() as patch:
        patch.setattr(Store, "add", fail_promotion)
        assert record_gap(f) == 2
    orphan = rows(f)[-1]
    assert orphan.source["type"] == "promotion_gap"
    assert matching_promotion("fixture", f.cat["apps"]["fixture"], rows(f), installed_programs([str(f.install)])) is None
    assert record_gap(f, install_gap=[], gap_record=["fixture=" + orphan.record_id]) == 0
    newer = replace(rows(f)[-1], source={"observed_by": "another fixture observer"},
                    recorded_at="2026-10-13T12:01:00Z", record_id="")
    Store(f.data / "records.jsonl").add(newer)
    assert_refused(f, install_gap=[], gap_record=["fixture=" + orphan.record_id])


@pytest.mark.parametrize("damage", ["verdict", "condition", "task", "check", "null-check"])
def test_decoder_and_store_pin_no_attribution(gap_case, damage):
    f = gap_case; assert record_gap(f) == 0
    record = copy.deepcopy(rows(f)[-2])
    if damage == "verdict": record.verdict = "fail"
    elif damage == "condition": record.condition_ids = ["claim"]
    elif damage == "task": record.task_ids = ["claim"]
    elif damage == "check": record.source["check"] = "smoke"
    elif damage == "null-check": record.source["check"] = None
    record.record_id = record.identity()
    before = (f.data / "records.jsonl").read_bytes()
    with pytest.raises(ValueError): install_gap.decode_record(record)
    with pytest.raises(ValueError): Store(f.data / "records.jsonl").add(record)
    assert (f.data / "records.jsonl").read_bytes() == before


def test_gap_cannot_be_laundered_into_smoke(gap_case):
    f = gap_case; assert record_gap(f) == 0
    gap = copy.deepcopy(rows(f)[-2])
    gap.source.update(check="smoke", check_fingerprint=check_fingerprint(f.cat["checks"]["smoke"]))
    gap.record_id = gap.identity()
    without_smoke = [r for r in rows(f) if not (r.revision == f.revision and r.source.get("check") == "smoke")]
    ids = [r for r in f.gate_ids if r in {x.record_id for x in without_smoke}] + [gap.record_id]
    with pytest.raises(ValueError, match="gate"):
        validate_promotion_member("fixture", f.cat["apps"]["fixture"], f.revision,
            f.mirrors.main_first_parent("fixture"), package_identity(f.pkg, [str(f.install)])[1],
            installed_programs([str(f.install)]), ids, ["src/app.py"], without_smoke + [gap],
            f.cat, {"fixture": f.revision}, "linux")


def test_coupled_preflight_failure_writes_no_gap(gap_case):
    f = gap_case
    second = copy.deepcopy(f.cat["apps"]["fixture"])
    second["promotion"]["programs"] = [str(f.root / "missing-second-program")]
    f.cat["apps"]["second"] = second
    f.cat["promotion_sets"] = [["fixture", "second"]]
    assert_refused(f, app=["fixture", "second"],
                   package=["fixture=" + str(f.pkg), "second=" + str(f.pkg)])


def test_coupled_success_is_one_promotion(gap_case):
    f = gap_case
    f.cat["apps"]["second"] = copy.deepcopy(f.cat["apps"]["fixture"])
    f.cat["promotion_sets"] = [["fixture", "second"]]
    assert record_gap(f, app=["fixture", "second"],
                      package=["fixture=" + str(f.pkg), "second=" + str(f.pkg)]) == 0
    gap, promotion = rows(f)[-2:]
    assert gap.source["type"] == "promotion_gap" and set(promotion.detail["members"]) == {"fixture", "second"}


def test_previously_deployed_reader_accepts_new_records_without_new_meaning(gap_case):
    f = gap_case;assert record_gap(f) == 0
    repo = Path(recorder.__file__).resolve().parents[1]
    old = f.root / "old-reader";old.mkdir()
    revision = "6b50df39b053a4c34fa597ae43d2f698b3754da8"
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", revision, "lcstatus"], cwd=repo, text=True).splitlines()
    for name in names:
        if not name.endswith(".py"): continue
        target = old / name;target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(subprocess.check_output(["git", "show", revision + ":" + name], cwd=repo))
    case = f.root / "old-reader-case.json"
    case.write_text(json.dumps({"store": str(f.data / "records.jsonl"), "row": f.cat["apps"]["fixture"],
                                "observed": installed_programs([str(f.install)])}))
    script = """import json,sys
from pathlib import Path
from lcstatus.evidence import Store
from lcstatus.rules import matching_promotion
c=json.loads(Path(sys.argv[1]).read_text());r=Store(Path(c['store'])).all()
matched=matching_promotion('fixture',c['row'],r,c['observed'])
orphan=matching_promotion('fixture',c['row'],r[:-1],c['observed'])
print(json.dumps({'match':matched.record_id,'orphan':None if orphan is None else orphan.record_id,
                  'identities':[x.identity() for x in r]}))
"""
    child = subprocess.run([sys.executable, "-c", script, str(case)], cwd=old, text=True, capture_output=True, check=True)
    result = json.loads(child.stdout)
    assert result["match"] == rows(f)[-1].record_id and result["orphan"] is None
    assert result["identities"] == [r.identity() for r in rows(f)]


@pytest.mark.parametrize("layout", ["program-map", "named-programs"])
@pytest.mark.parametrize("damage", [None, "package-only", "not-installed", "source", "ambiguous", "no-observation"])
def test_retained_ew_receipt_adapters_are_source_bound(gap_case, layout, damage):
    f = gap_case;payload = copy.deepcopy(f.payload)
    payload.update(app="email-watcher", repo="eom-email-watcher")
    event = payload["unrecorded_installs"][-1];payload["unrecorded_installs"] = [event]
    contents = {"/usr/bin/eom-email-watcher-desktop": b"desktop", "/usr/bin/eom-mail-engine": b"engine"}
    package_path = package(f.root / "ew.tar", contents)
    event.update(package=str(package_path), package_sha256=sha(package_path),
                 program_hashes={k: hashlib.sha256(v).hexdigest() for k, v in contents.items()})
    p = {"source": event["revision"]}
    if layout == "program-map":
        p.update(package_sha256=event["package_sha256"], program_hashes={k.lstrip("/"): v for k, v in event["program_hashes"].items()})
    else:
        p.update(deb_sha256=event["package_sha256"], desktop_sha256=event["program_hashes"]["/usr/bin/eom-email-watcher-desktop"],
                 engine_sha256=event["program_hashes"]["/usr/bin/eom-mail-engine"])
    acceptance = {"source": event["revision"], "verified_at": event["installed_at"],
                  "installed_bytes_match": True, "linux_installed_acceptance": "PASS"}
    if damage == "not-installed": acceptance["installed_bytes_match"] = False
    if damage == "source": acceptance["source"] = "b" * 40
    if damage == "ambiguous":
        p.update(package_sha256=event["package_sha256"], program_hashes=event["program_hashes"],
                 deb_sha256=event["package_sha256"], desktop_sha256="a" * 64, engine_sha256="a" * 64)
    event["receipts"] = []
    for format, content in [("ew-package", p), ("ew-acceptance", acceptance)]:
        if format == "ew-acceptance" and damage == "package-only": continue
        path = f.root / (format + ".json");path.write_text(json.dumps(content))
        event["receipts"].append({"format": format, "path": str(path), "sha256": sha(path)})
    witness = Record(kind="installed_observation", repo="eom-email-watcher", revision="", verdict="pass", platform="linux",
                     source={"type": "installed_program_hashes", "app": "email-watcher"},
                     detail={"app": "email-watcher", "installed": {"hashes": event["program_hashes"], "missing": [], "errors": {}}},
                     recorded_at=event["installed_at"])
    event["installed_observation_record_ids"] = [] if damage == "no-observation" else [witness.record_id]
    base = replace(f.predecessor, detail={"members": {"email-watcher": f.predecessor.detail["members"]["fixture"]}})
    candidate = {k: "d" * 64 for k in event["program_hashes"]}
    def verify():
        install_gap.Gap.decode(payload).verify_history(base, NOW, [base, witness], candidate)
    if damage in (None, "package-only"):
        verify()
    else:
        with pytest.raises(ValueError): verify()


@pytest.mark.parametrize("damage", ["missing", "wrong-app", "payload", "fallback", "empty-link", "removed-link", "identity", "window", "stale"])
def test_gap_link_controls_promoted_label(gap_case, damage):
    f = gap_case; assert record_gap(f) == 0
    records = rows(f); gap, promotion = records[-2:]
    if damage == "missing": records.remove(gap)
    elif damage == "wrong-app": gap.source["install_gap"]["app"] = "other"
    elif damage == "payload": gap.source["install_gap"]["history_attested_complete"] = False
    elif damage == "fallback": promotion.detail["members"]["fixture"]["fallback"]["sha256"] = "b" * 64
    elif damage == "empty-link": promotion.detail["members"]["fixture"]["fallback"]["install_gap_record_id"] = ""
    elif damage == "removed-link": del promotion.detail["members"]["fixture"]["fallback"]["install_gap_record_id"]
    elif damage == "identity": gap.source["observed_by"] = "unbound replacement observer"
    elif damage == "window": gap.source["install_gap"]["unrecorded_installs"][-1]["installed_at"] = "2026-11-01T00:00:00Z"
    elif damage == "stale":
        peer = replace(f.predecessor, source={"observed_by": "peer"}, recorded_at="2026-10-13T11:59:00Z", record_id="")
        records.insert(-1, peer)
    if damage in ["wrong-app", "payload", "window"]:
        gap.record_id = gap.identity()
        promotion.detail["members"]["fixture"]["fallback"]["install_gap_record_id"] = gap.record_id
    promotion.record_id = promotion.identity()
    assert matching_promotion("fixture", f.cat["apps"]["fixture"], records, installed_programs([str(f.install)])) is None


@pytest.mark.parametrize("gap_case", ["docs-only"], indirect=True)
def test_standing_interval_starts_at_recorded_predecessor(gap_case):
    f = gap_case
    short = [r for r in f.gate_ids if next(x for x in rows(f) if x.record_id == r).source["check"] != "standing"]
    assert_refused(f, gate_record=short)
    assert record_gap(f) == 0


def test_package_snapshot_is_the_bytes_that_are_interpreted(tmp_path, monkeypatch):
    from lcstatus import promotion
    path = package(tmp_path / "package.tar", {"/usr/bin/fixture": b"reviewed"})
    original_sha = sha(path); real = promotion.read_regular
    def replace_after_snapshot(name):
        content = real(name)
        package(path, {"/usr/bin/fixture": b"replacement"})
        return content
    monkeypatch.setattr(promotion, "read_regular", replace_after_snapshot)
    digest, hashes = package_identity(path, ["/usr/bin/fixture"])
    assert digest == original_sha and sha(path) != digest
    assert hashes == {"/usr/bin/fixture": hashlib.sha256(b"reviewed").hexdigest()}


def test_real_debian_package_snapshot_uses_stdin(tmp_path):
    package_path = debian_package(tmp_path, {"/usr/bin/fixture": b"not executable"})
    digest, hashes = package_identity(package_path, ["/usr/bin/fixture"])
    assert digest == sha(package_path)
    assert hashes == {"/usr/bin/fixture": hashlib.sha256(b"not executable").hexdigest()}


def test_special_evidence_file_refuses_in_a_fresh_process_without_blocking(tmp_path):
    fifo = tmp_path / "evidence.fifo";os.mkfifo(fifo)
    script = "from pathlib import Path; from lcstatus.promotion import read_regular; read_regular(Path(__import__('sys').argv[1]))"
    child = subprocess.run([sys.executable, "-c", script, str(fifo)], capture_output=True, text=True, timeout=5)
    assert child.returncode != 0 and "must be a regular file" in child.stderr


@pytest.mark.parametrize("kind", ["duplicate", "symlink"])
def test_duplicate_or_nonregular_package_program_refuses(tmp_path, kind):
    path = package(tmp_path / "unsafe.tar", {"/usr/bin/fixture": b"first"})
    with tarfile.open(path, "a") as archive:
        entry = tarfile.TarInfo("usr/bin/fixture")
        entry.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.REGTYPE
        entry.linkname = "/outside-sentinel"
        archive.addfile(entry)
    with pytest.raises(ValueError, match="duplicate or nonregular"):
        package_identity(path, ["/usr/bin/fixture"])


def test_evidence_path_is_never_executed(gap_case):
    f = gap_case;marker = f.root / "must-not-exist"
    script = f.root / "receipt.sh";script.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\n");script.chmod(0o700)
    ref = f.payload["unrecorded_installs"][-1]["receipts"][0]
    ref.update(path=str(script), sha256=sha(script))
    assert_refused(f)
    assert not marker.exists()


def test_long_valid_reinstall_history_is_admitted(gap_case):
    f = gap_case;first, last = f.payload["unrecorded_installs"]
    extra = []
    for minute in range(1, 11):
        event = copy.deepcopy(first);event["installed_at"] = f"2026-10-11T12:{minute:02d}:00+00:00"
        witness = json.loads(Path(first["receipts"][0]["path"]).read_text())
        witness["installed_at"] = event["installed_at"]
        path = f.root / f"reinstall-{minute}.json";path.write_text(json.dumps(witness))
        event["receipts"] = [{"format": "installed-v1", "path": str(path), "sha256": sha(path)}]
        extra.append(event)
    f.payload["unrecorded_installs"] = [first, *extra, last]
    assert record_gap(f) == 0


@pytest.mark.parametrize("damage", [None, "missing", "hash", "time", "after-gap", "later-tick", "late-backdated"])
def test_collector_link_is_pure_and_bounded_to_publication(gap_case, damage, monkeypatch):
    f = gap_case
    witnesses = []
    for event in f.payload["unrecorded_installs"]:
        row = Record(kind="installed_observation", repo="fixture", revision="", verdict="pass", platform="linux",
            source={"type": "installed_program_hashes", "app": "fixture"},
            detail={"app": "fixture", "installed": {"hashes": event["program_hashes"], "missing": [], "errors": {}}},
            recorded_at=event["installed_at"])
        Store(f.data / "records.jsonl").add(row)
        event["installed_observation_record_ids"] = [row.record_id]
        witnesses.append(row.record_id)
    assert record_gap(f) == 0
    retained = rows(f)
    witness = next(r for r in retained if r.record_id == witnesses[0])
    if damage == "missing": retained.remove(witness)
    elif damage == "hash": witness.detail["installed"]["hashes"] = {str(f.install): "f" * 64}
    elif damage == "time": witness.recorded_at = "2026-11-01T00:00:00Z"
    elif damage == "after-gap":
        retained.remove(witness);retained.insert(-1, witness)
    elif damage in ("later-tick", "late-backdated"):
        later = replace(witness, recorded_at="2026-11-01T00:00:00Z" if damage == "later-tick" else witness.recorded_at,
                        detail={"app": "fixture", "installed": {"hashes": {str(f.install): "f" * 64}, "missing": [], "errors": {}}}, record_id="")
        retained.append(later)
    def no_file(*_): raise AssertionError("stored matching reopened a receipt")
    monkeypatch.setattr(install_gap, "read_regular", no_file)
    match = matching_promotion("fixture", f.cat["apps"]["fixture"], retained, installed_programs([str(f.install)]))
    assert (match is not None) == (damage in (None, "later-tick", "late-backdated"))
