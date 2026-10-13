"""Contract 10: only fake install trees, fixture packages and fixture Git repositories."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from lcstatus import collect
from lcstatus.catalogue import load
from lcstatus.evidence import Record, Store, check_fingerprint
from lcstatus.promotion import installed_observation, installed_programs, package_programs, revision_pins
from lcstatus.render import render_all
from lcstatus.rules import installed_statuses, matching_promotion, required_promotion_checks, task_status
from lcstatus.sources import Failure, Mirrors
from scripts import record_observation as recorder


NOW = "2026-10-13T12:00:00+00:00"
PIN = "a" * 40


def git(root: Path, *args: str, date: str = "2026-10-10T12:00:00+00:00") -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    result = subprocess.run(["git", "-C", str(root), *args], env=env,
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def package(path: Path, programs: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w") as archive:
        for name, content in programs.items():
            entry = tarfile.TarInfo(name.lstrip("/"))
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    return path


class GitHub:
    def __init__(self):
        self.labels = {}

    def api(self, path, **kwargs):
        sha = path.split("/")[-2]
        return [{"number": 31, "merge_commit_sha": sha, "merged_at": NOW,
                 "labels": [{"name": n} for n in self.labels.get(sha, [])]}]


@pytest.fixture
def fixture(tmp_path):
    work = tmp_path / "product"; work.mkdir()
    git(work, "init", "-b", "main")
    git(work, "config", "user.email", "fixture@example.invalid")
    git(work, "config", "user.name", "Fixture")
    (work / "src").mkdir(); (work / "src" / "app.py").write_text("initial")
    (work / "uv.lock").write_text(f'''version = 1
[[package]]
name = "connect-automate"
version = "0.1.0"
source = {{ git = "https://github.com/canfieldjuan/connect-automate?rev={PIN}#{PIN}" }}
''')
    git(work, "add", "."); git(work, "commit", "-m", "initial")
    revision = git(work, "rev-parse", "HEAD")
    mirrors_root = tmp_path / "mirrors"; mirrors_root.mkdir()
    git(tmp_path, "clone", "--mirror", str(work), str(mirrors_root / "fixture.git"))
    install = tmp_path / "fake-install" / "program"; install.parent.mkdir(); install.write_bytes(b"promoted-program")
    config = {"programs": [str(install)], "shipped_paths": ["src/**", "uv.lock"],
              "smoke": {"argv": [str(install), "smoke"]}, "smoke_check": "smoke",
              "automated_checks": ["tests"], "standing_gates": [{"check": "standing", "paths": ["src/**"]}],
              "lag_limit": {"days": 7}, "pin_sources": [{"format": "uv", "path": "uv.lock",
                                                         "dependencies": {"connect-automate": "sdk"}}]}
    cat = {"release": {"target": "fixture", "automate_scope": {"decision": "test", "note": "fixture"}},
           "repos": {"fixture": {"github": "fixture/fixture"}},
           "apps": {"fixture": {"name": "Fixture", "repo": "fixture", "promotion": config}},
           "checks": {"tests": {"runner": "pytest", "repo": "fixture", "platform": "linux", "args": []},
                      "smoke": {"runner": "manual_observation", "repo": "fixture", "platform": "linux"},
                      "standing": {"runner": "manual_observation", "repo": "fixture", "platform": "linux"}},
           "tasks": [], "promotion_sets": [["fixture"]]}
    data = tmp_path / "data"; data.mkdir()
    store = Store(data / "records.jsonl")
    gates = []
    for check_id, kind in (("tests", "automated_test"), ("smoke", "installed_demo"), ("standing", "installed_demo")):
        gate = Record(kind=kind, repo="fixture", revision=revision, verdict="pass", platform="linux",
                      executed=2 if kind == "automated_test" else None, failed=0 if kind == "automated_test" else None,
                      recorded_at="2026-10-10T12:01:00+00:00",
                      source={"check": check_id, "check_fingerprint": check_fingerprint(cat["checks"][check_id]),
                              "observed_by": "Fixture human", "artifact": "fake-smoke-receipt"})
        store.add(gate); gates.append(gate)
    pkg = package(tmp_path / "package.tar", {str(install): install.read_bytes()})
    return SimpleNamespace(root=tmp_path, work=work, revision=revision, mirrors_root=mirrors_root,
                           mirrors=Mirrors(mirrors_root, cat["repos"]), install=install, cat=cat,
                           config=config, data=data, store=store, gates=gates, pkg=pkg, gh=GitHub())


def args(f, **changes):
    values = dict(app=["fixture"], participant=["fixture=" + f.revision],
                  package=["fixture=" + str(f.pkg)], rollback_package=[],
                  gate_record=[r.record_id for r in f.gates], verdict="pass", platform="linux",
                  observed_by="Fixture human", artifact="retained fixture receipt", summary="fixture")
    return SimpleNamespace(**(values | changes))


def record(f, **changes):
    return recorder.record_promotion(args(f, **changes), f.cat, NOW, f.data, f.mirrors_root)


def rows(f):
    return Store(f.data / "records.jsonl").all()


def gates_at(f, revision):
    gates = [Record(**{**json.loads(gate.to_json()), "revision": revision, "record_id": ""}) for gate in f.gates]
    Store(f.data / "records.jsonl").add_all(gates)
    return [gate.record_id for gate in gates]


def observe(f):
    snapshot = installed_observation("fixture", f.cat["apps"]["fixture"], f.cat,
                                     rows(f), f.mirrors, f.gh, {"fixture": git(f.work, "rev-parse", "main")})
    Store(f.data / "records.jsonl").add(snapshot)
    return installed_statuses(f.cat, rows(f), at=NOW)[0]


@pytest.mark.parametrize("refusal", ["hash", "off_main", "other_revision", "missing"])
def test_p1_four_refusals_write_nothing(fixture, refusal, capsys):
    f = fixture; changes = {}
    if refusal == "hash":
        f.install.write_bytes(b"qualification-test-build")
    elif refusal == "off_main":
        git(f.work, "switch", "-c", "candidate")
        (f.work / "src" / "app.py").write_text("candidate")
        git(f.work, "commit", "-am", "candidate")
        branch_revision = git(f.work, "rev-parse", "HEAD")
        git(f.mirrors.path("fixture"), "fetch", "origin")
        changes["participant"] = ["fixture=" + branch_revision]
        changes["gate_record"] = gates_at(f, branch_revision)
    elif refusal == "other_revision":
        wrong = Record(**{**json.loads(f.gates[0].to_json()), "revision": "b" * 40, "record_id": ""})
        Store(f.data / "records.jsonl").add(wrong)
        changes["gate_record"] = [wrong.record_id] + [r.record_id for r in f.gates[1:]]
    else:
        changes["gate_record"] = ["missing-record"]
    before = (f.data / "records.jsonl").read_bytes()
    assert record(f, **changes) == 2
    assert (f.data / "records.jsonl").read_bytes() == before
    assert capsys.readouterr().err


def test_p1_second_parent_revision_is_not_main_first_parent(fixture):
    f = fixture
    git(f.work, "switch", "-c", "side")
    (f.work / "side.txt").write_text("branch")
    git(f.work, "add", "."); git(f.work, "commit", "-m", "side")
    side = git(f.work, "rev-parse", "HEAD")
    git(f.work, "switch", "main"); git(f.work, "merge", "--no-ff", "side", "-m", "merge")
    git(f.mirrors.path("fixture"), "fetch", "origin")
    before = (f.data / "records.jsonl").read_bytes()
    ids = gates_at(f, side)
    before = (f.data / "records.jsonl").read_bytes()
    assert record(f, participant=["fixture=" + side], gate_record=ids) == 2
    assert (f.data / "records.jsonl").read_bytes() == before


@pytest.mark.parametrize("gate_change", ["missing_smoke", "failed", "skip", "zero_tests", "fingerprint", "unobserved"])
def test_p3_no_waiver_for_bootstrap_gate_results(fixture, gate_change):
    f = fixture
    gate = f.gates[1] if gate_change in ("missing_smoke", "unobserved") else f.gates[0]
    changes = json.loads(gate.to_json()); changes["record_id"] = ""; changes["recorded_at"] = "2026-10-10T12:02:00+00:00"
    if gate_change == "failed": changes["verdict"] = "fail"
    elif gate_change == "skip": changes["verdict"] = "skip"
    elif gate_change == "zero_tests": changes["executed"] = 0
    elif gate_change == "fingerprint": changes["source"]["check_fingerprint"] = "wrong"
    elif gate_change == "unobserved": changes["source"].pop("observed_by")
    store = Store(f.data / "records.jsonl")
    newer = Record(**changes)
    if gate_change != "missing_smoke": store.add(newer)
    ids = [r.record_id for r in f.gates if r != gate]
    if gate_change != "missing_smoke": ids.append(newer.record_id)
    before = (f.data / "records.jsonl").read_bytes()
    assert record(f, gate_record=ids) == 2
    assert (f.data / "records.jsonl").read_bytes() == before


def test_starting_point_and_real_previous_fallback(fixture):
    f = fixture
    assert record(f) == 0
    first = rows(f)[-1]
    member = first.detail["members"]["fixture"]
    assert member["fallback"]["starting_point"] is True
    assert member["fallback"]["sha256"] == member["package_sha256"]
    assert record(f) == 2  # later record cannot silently fall back to itself
    assert record(f, rollback_package=["fixture=" + str(f.pkg)]) == 0
    assert rows(f)[-1].detail["members"]["fixture"]["fallback"]["previous_record"] == first.record_id
    wrong = package(f.root / "wrong.tar", {str(f.install): b"other-program"})
    before = (f.data / "records.jsonl").read_bytes()
    assert record(f, rollback_package=["fixture=" + str(wrong)]) == 2
    assert (f.data / "records.jsonl").read_bytes() == before


def test_smoke_failure_records_failure_and_no_promotion(fixture):
    f = fixture
    # The product/operator owns reinstall. This recorder records their failed observation;
    # it never tries to launch a program or perform rollback itself.
    failed = Record(**{**json.loads(f.gates[1].to_json()), "verdict": "fail", "record_id": "",
                       "recorded_at": "2026-10-10T13:00:00+00:00"})
    Store(f.data / "records.jsonl").add(failed)
    assert record(f, gate_record=[f.gates[0].record_id, failed.record_id, f.gates[2].record_id]) == 2
    assert not any(r.kind == "promotion" for r in rows(f))


def test_p2_qualification_replacement_and_reinstall_twin(fixture):
    f = fixture
    assert observe(f)["label"] == "unrecorded install"
    assert record(f) == 0
    promoted = observe(f)
    assert promoted["label"] == "promoted" and promoted["current"]
    before = f.install.read_bytes()
    f.install.write_bytes(b"qualification-build")
    replaced = observe(f)
    assert replaced["label"] == "unrecorded install" and replaced["color"] == "red"
    f.install.write_bytes(before)
    assert observe(f)["label"] == "promoted"


def test_p2_missing_promoted_program_is_red(fixture):
    f = fixture; assert record(f) == 0
    f.install.unlink()
    status = observe(f)
    assert status["label"] == "promoted build missing" and status["color"] == "red"
    assert not status["current"]


def test_p2_not_installed_and_io_unavailable(fixture, monkeypatch):
    f = fixture; f.install.unlink()
    assert observe(f)["label"] == "not installed"
    def denied(path):
        raise PermissionError("fixture permission failure")
    monkeypatch.setattr("lcstatus.promotion.sha256_file", denied)
    item = observe(f)
    assert item["label"] == "unavailable" and not item["current"]
    assert item["errors"]


def test_folder_name_never_supplies_promotion(fixture):
    f = fixture
    # The package sits in an evidence directory named as a promotional assertion. No P1 exists.
    folder = f.root / "promoted-c698339"; folder.mkdir()
    f.pkg.rename(folder / f.pkg.name)
    f.install.rename(folder / "program")
    f.install = folder / "program"
    f.config["programs"] = [str(f.install)]
    status = observe(f)
    assert status["label"] == "unrecorded install" and status["color"] == "red"


def merge(f, path, date="2026-10-12T12:00:00+00:00"):
    branch = "branch" + str(len(git(f.work, "log", "--oneline").splitlines()))
    git(f.work, "switch", "-c", branch)
    target = f.work / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(branch)
    git(f.work, "add", "."); git(f.work, "commit", "-m", "work", date=date)
    git(f.work, "switch", "main"); git(f.work, "merge", "--no-ff", branch, "-m", "merge", date=date)
    git(f.mirrors.path("fixture"), "fetch", "origin")
    return git(f.work, "rev-parse", "main")


def test_p4_first_parent_shipped_merges_exclude_docs_contracts_tests(fixture):
    f = fixture; assert record(f) == 0
    for path in ("docs/manual.md", "contracts/protocol.json", "tests/test_app.py",
                 "desktop/test/runtime.test.ts", "src/docs/notes.md"):
        merge(f, path)
    # Even a broad shipped glob cannot count evidence-only merges.
    f.config["shipped_paths"] = ["**"]
    promotion = next(r for r in rows(f) if r.kind == "promotion")
    promotion.detail["members"]["fixture"]["config"] = check_fingerprint(f.config)
    # Fixture records are rebuilt in a private store; production records are never edited.
    private = f.root / "broad-records"; private.mkdir()
    store = Store(private / "records.jsonl"); store.add_all([promotion])
    f.data = private
    current = observe(f)
    assert current["lag"] == "current" and not current["merges"]
    shipped = merge(f, "src/app.py")
    behind = observe(f)
    assert behind["lag"] == "behind" and behind["color"] == "amber"
    assert [m["sha"] for m in behind["merges"]] == [shipped]
    assert behind["merges"][0]["prs"] == [31]


@pytest.mark.parametrize("label", ["security", "data-loss"])
def test_p4_urgent_labels_go_red_immediately(fixture, label):
    f = fixture; assert record(f) == 0
    sha = merge(f, "src/app.py")
    f.gh.labels[sha] = [label]
    status = observe(f)
    assert status["lag"] == "stale" and status["color"] == "red"


def test_p4_seven_day_boundary_and_unavailable_history(fixture):
    f = fixture; assert record(f) == 0
    merge(f, "src/app.py", date="2026-10-05T12:00:00+00:00")
    observe(f)
    boundary = installed_statuses(f.cat, rows(f), at="2026-10-12T12:00:00+00:00")[0]
    assert boundary["lag"] == "behind"
    stale = installed_statuses(f.cat, rows(f), at=NOW)[0]
    assert stale["lag"] == "stale" and stale["color"] == "red"
    f.gh.api = lambda *a, **kw: Failure("github", "fixture outage")
    unavailable = observe(f)
    assert unavailable["lag"] == "unavailable" and not unavailable["current"]


def test_p5_pins_read_exact_promoted_revision_not_main(fixture):
    f = fixture; assert record(f) == 0
    git(f.work, "switch", "-c", "pins")
    (f.work / "uv.lock").write_text((f.work / "uv.lock").read_text().replace(PIN, "b" * 40))
    git(f.work, "commit", "-am", "lock")
    git(f.work, "switch", "main"); git(f.work, "merge", "--no-ff", "pins", "-m", "merge")
    git(f.mirrors.path("fixture"), "fetch", "origin")
    status = observe(f)
    assert status["pins"]["values"] == {"sdk": [PIN]}


def test_p5_disagreement_on_all_participants_clears_with_one_set_record(fixture):
    f = fixture; assert record(f) == 0
    first = next(r for r in rows(f) if r.kind == "promotion")
    second_row = json.loads(json.dumps(f.cat["apps"]["fixture"]))
    second_row["name"] = "Second"; second_row["repo"] = "second"
    f.cat["apps"]["second"] = second_row
    f.cat["promotion_sets"] = [["fixture", "second"]]
    member = json.loads(json.dumps(first.detail["members"]["fixture"])); member["repo"] = "second"
    second = Record(kind="promotion", repo="second", revision="b" * 40, participants={"second": "b" * 40},
                    verdict="pass", platform="linux", recorded_at=NOW,
                    source={"observed_by": "Fixture human"}, detail={"members": {"second": member}})
    observed = installed_programs([str(f.install)])
    a = Record(kind="installed_observation", repo="fixture", revision=f.revision, verdict="pass", platform="linux",
               detail={"app": "fixture", "installed": observed, "promotion_id": first.record_id,
                       "pins": {"values": {"sdk": [PIN]}}, "merges": []})
    b = Record(kind="installed_observation", repo="second", revision="b" * 40, verdict="pass", platform="linux",
               detail={"app": "second", "installed": observed, "promotion_id": second.record_id,
                       "pins": {"values": {"sdk": ["b" * 40]}}, "merges": []})
    mismatched = installed_statuses(f.cat, [first, second, a, b], at=NOW)
    assert all(s["coupled"] == "coupled-set mismatch" and s["color"] == "red" for s in mismatched)
    combined = Record(kind="promotion", repo="fixture", revision=f.revision,
                      participants={"fixture": f.revision, "second": "b" * 40}, verdict="pass", platform="linux",
                      recorded_at="2026-10-13T12:01:00+00:00", source={"observed_by": "Fixture human"},
                      detail={"members": {"fixture": first.detail["members"]["fixture"], "second": member}})
    a.detail["promotion_id"] = b.detail["promotion_id"] = combined.record_id
    cleared = installed_statuses(f.cat, [first, second, combined, a, b], at=NOW)
    assert all(s["coupled"] == "clear" and s["current"] for s in cleared)


def test_superseded_and_collection_lock_for_atomic_sets(fixture, monkeypatch):
    f = fixture; assert record(f) == 0
    assert record(f, rollback_package=["fixture=" + str(f.pkg)]) == 0
    status = observe(f)
    promotions = [r for r in rows(f) if r.kind == "promotion"]
    assert status["promotion_id"] == promotions[-1].record_id
    assert status["superseded"] == [promotions[0].record_id]
    # Exercise the CLI's existing common serialization owner, not an alternate promotion lock.
    captured = []
    monkeypatch.setattr(recorder, "collection_lock", lambda path, wait: captured.append((path, wait)))
    monkeypatch.setattr(recorder.catmod, "load", lambda _: f.cat)
    monkeypatch.setattr(sys, "argv", ["record", "--kind", "promotion", "--app", "fixture",
                                     "--participant", "fixture=" + f.revision, "--verdict", "pass",
                                     "--platform", "linux", "--artifact", "fake", "--summary", "fake",
                                     "--observed-at", NOW, "--observed-by", "Fixture human",
                                     "--data", str(f.data), "--mirrors", str(f.mirrors_root)])
    assert recorder.main() == 2  # missing package; lock still precedes the catalogue and refusal
    assert captured == [(f.data, True)]


def test_read_only_observation_does_not_execute_smoke_or_change_tree(fixture, monkeypatch):
    f = fixture; assert record(f) == 0
    before = {p: p.read_bytes() for p in f.install.parent.rglob("*") if p.is_file()}
    calls = []
    run = subprocess.run
    def observe_run(command, **kwargs):
        calls.append(command)
        assert command[0] == "git", "collector cannot execute a product or install command"
        return run(command, **kwargs)
    monkeypatch.setattr(subprocess, "run", observe_run)
    assert observe(f)["label"] == "promoted"
    assert calls
    assert {p: p.read_bytes() for p in f.install.parent.rglob("*") if p.is_file()} == before


def test_all_installed_labels_render_and_promotions_never_release_evidence(fixture):
    f = fixture; assert record(f) == 0
    observe(f)
    site = f.root / "site"
    render_all(site, f.cat, [], rows(f), {"fixture": f.revision}, [], {"last_run_at": NOW}, {})
    payload = json.loads((site / "status.json").read_text())
    assert payload["installed_apps"][0]["label"] == "promoted"
    for file in ("dashboard.html", "report.md"):
        assert "Installed builds" in (site / file).read_text()
        assert "promoted" in (site / file).read_text()
    check = {"repo": "fixture", "runner": "manual_observation", "platform": "linux"}
    cat = {**f.cat, "checks": {"demo": check}}
    task = {"id": "release", "app": "fixture", "layer": "release",
            "conditions": [{"id": "demo", "kind": "installed_demo", "check": "demo"}]}
    status = task_status(task, rows(f), {"fixture": f.revision}, cat, {})
    assert status.conditions[0].state == "no_evidence"
    assert status.maturity == "planned"


def test_standing_gate_trigger_and_catalogue_are_valid(fixture):
    f = fixture
    assert "standing" not in required_promotion_checks(f.config, ["docs/readme.md"])
    assert "standing" in required_promotion_checks(f.config, ["src/app.py"])
    path = f.root / "catalogue.json"; path.write_text(json.dumps(f.cat)); load(path)
    assert package_programs(f.pkg, [str(f.install)]) == installed_programs([str(f.install)])["hashes"]


def test_pin_sources_require_full_locked_git_sha(fixture):
    f = fixture
    class Inexact:
        def show(self, *args):
            return '[[package]]\nname="connect-automate"\nsource={git="https://example.invalid/sdk#main"}\n'
    with pytest.raises(ValueError, match="exact locked revision"):
        revision_pins(Inexact(), "fixture", f.revision, f.config)


def test_p5_contract_schema_and_protocol_pins_are_data_not_folder_labels(fixture):
    import hashlib
    f = fixture
    schema = json.dumps({"properties": {"protocol_version": {"const": 2}}})
    pin = json.dumps({"repository": "canfieldjuan/connect-contracts", "commit": PIN,
                      "files": {"schema.json": {"source_path": "misleading-v1/schema.json",
                                                "sha256": hashlib.sha256(schema.encode()).hexdigest()}}})
    class Locks:
        def show(self, repo, revision, path):
            assert revision in (f.revision, PIN)
            return pin if repo == "fixture" else schema
    config = {"pin_sources": [{"format": "contracts", "path": "pin.json"}]}
    pins = revision_pins(Locks(), "fixture", f.revision, config)
    assert pins["values"]["protocol"] == ["2"]
    assert pins["values"]["connect-contracts"] == [PIN]
    class Changed(Locks):
        def show(self, repo, revision, path):
            return pin if repo == "fixture" else schema + " "
    with pytest.raises(ValueError, match="hash mismatch"):
        revision_pins(Changed(), "fixture", f.revision, config)


def test_collector_tick_and_render_only_preserve_installed_observation(fixture, monkeypatch):
    f = fixture; assert record(f) == 0
    path = f.root / "catalogue.json"; path.write_text(json.dumps(f.cat))
    monkeypatch.setattr(collect, "CACHE", f.root / "private-cache")
    monkeypatch.setattr(collect, "Mirrors", lambda *a, **kw: f.mirrors)
    f.gh.default_branch_head = lambda _: {"sha": f.revision}
    monkeypatch.setattr(collect, "GitHub", lambda: f.gh)
    site = f.root / "site"
    command = ["--catalogue", str(path), "--data", str(f.data), "--site", str(site), "--no-fetch", "--no-local"]
    assert collect.main(command) == 0
    first = json.loads((site / "status.json").read_text())["installed_apps"]
    assert first[0]["label"] == "promoted"
    f.install.write_bytes(b"another-installed-build")
    # Render-only uses the stored snapshot without reading any installed bytes.
    assert collect.main(command + ["--render-only"]) == 0
    assert json.loads((site / "status.json").read_text())["installed_apps"] == first
    assert collect.main(command) == 0
    last = json.loads((site / "status.json").read_text())["installed_apps"]
    assert last[0]["label"] == "unrecorded install" and last[0]["color"] == "red"


@pytest.mark.parametrize("case", ["unrecorded", "missing", "not_installed", "unavailable"])
def test_every_install_label_is_rendered_in_all_outputs(fixture, monkeypatch, case):
    f = fixture
    if case == "missing":
        assert record(f) == 0; f.install.unlink()
    elif case == "not_installed": f.install.unlink()
    elif case == "unavailable":
        def denied(_): raise OSError("fixture I/O failure")
        monkeypatch.setattr("lcstatus.promotion.sha256_file", denied)
    expected = {"unrecorded": "unrecorded install", "missing": "promoted build missing",
                "not_installed": "not installed", "unavailable": "unavailable"}[case]
    assert observe(f)["label"] == expected
    site = f.root / "site"
    render_all(site, f.cat, [], rows(f), {}, [], {"last_run_at": NOW}, {})
    for name in ("status.json", "report.md", "dashboard.html"):
        assert expected in (site / name).read_text()
