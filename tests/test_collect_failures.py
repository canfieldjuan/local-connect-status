"""Source failures must be loud. A repo that cannot be read, or a GitHub that cannot be asked,
produces explicit failure records and a non-zero exit — never a clean, empty, green report."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

TINY_CATALOGUE = {
    "catalogue_version": 1,
    "release": {"target": "t", "required_platforms": ["linux", "windows"],
                "automate_scope": {"decision": "undecided", "note": "n", "required_for_first_release": None}},
    "repos": {"ghost": {"github": "canfieldjuan/this-repository-does-not-exist-9f3a", "ci_workflows": ["CI"]}},
    "apps": {"ghost-app": {"name": "Ghost", "repo": "ghost"}},
    "checks": {
        "g.ci": {"runner": "ci_job", "repo": "ghost", "workflow": "CI", "job": "test", "platform": "linux"},
        "g.rel": {"runner": "github_release", "repo": "*", "platform": "n/a"},
    },
    "tasks": [{
        "id": "g.task", "app": "ghost-app", "layer": "standalone", "title": "Ghost works", "promise": "p",
        "conditions": [{"id": "g.c1", "kind": "ci_run", "check": "g.ci", "proves": "ci"},
                       {"id": "g.c2", "kind": "release_artifact", "check": "g.rel", "proves": "rel"}],
        "depends_on": [{"repo": "ghost", "paths": ["src/**"]}],
    }],
}


def run_collect(tmp: Path, env_extra: dict[str, str]) -> tuple[int, dict, list[dict]]:
    cat = tmp / "catalogue.json"
    cat.write_text(json.dumps(TINY_CATALOGUE))
    data, site = tmp / "data", tmp / "site"
    env = dict(os.environ, **env_extra)
    # run from a copy of the package with an isolated cache so no real mirror is touched
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.argv=['collect']+sys.argv[1:]; "
                        "import lcstatus.collect as c; c.CACHE=__import__('pathlib').Path(sys.argv[-1]); sys.argv.pop(); "
                        "sys.exit(c.main(sys.argv[1:]))",
                        "--catalogue", str(cat), "--data", str(data), "--site", str(site), str(tmp / "cache")],
                       cwd=ROOT, capture_output=True, text=True, env=env, timeout=600)
    status = json.loads((site / "status.json").read_text()) if (site / "status.json").exists() else {}
    records = [json.loads(l) for l in (data / "records.jsonl").read_text().splitlines()] if (data / "records.jsonl").exists() else []
    return r.returncode, status, records


def test_missing_repository_and_unavailable_github_are_explicit(tmp_path: Path):
    # git available, gh not: GitHub is "unavailable", not "empty". The repository does not
    # exist, and with no terminal to prompt on, the clone fails at GitHub rather than hanging.
    import shutil
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    os.symlink(shutil.which("git"), bin_dir / "git")
    rc, status, records = run_collect(tmp_path, {"PATH": str(bin_dir), "GIT_TERMINAL_PROMPT": "0"})
    fetch_fail = next(r for r in records if r["kind"] == "collection_failure" and r["source"]["type"] == "git_fetch")
    assert "FileNotFoundError" not in fetch_fail["summary"], "git itself must have run"
    assert rc == 2, "a run with unreadable sources must not exit 0"
    kinds = [r["kind"] for r in records]
    assert "collection_failure" in kinds
    fails = [r for r in records if r["kind"] == "collection_failure"]
    assert any(r["source"]["type"] in ("git_fetch", "git_head") for r in fails)
    assert status["source_failures"], "the report must name what could not be read"
    task = status["tasks"][0]
    # nothing green: no revision was observed, so no condition can be satisfied
    assert task["maturity"] == "planned"
    assert all(c["state"] in ("no_evidence", "inconclusive") for c in task["conditions"])
    assert task["freshness"] == "no_evidence"


def test_render_only_never_invents_data(tmp_path: Path):
    cat = tmp_path / "catalogue.json"
    cat.write_text(json.dumps(TINY_CATALOGUE))
    r = subprocess.run([sys.executable, "-m", "lcstatus.collect", "--catalogue", str(cat), "--data", str(tmp_path / "d"),
                        "--site", str(tmp_path / "s"), "--render-only"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0
    status = json.loads((tmp_path / "s" / "status.json").read_text())
    assert status["generated_at"] is None and status["heads"] == {}
    assert status["tasks"][0]["maturity"] == "planned"



def test_render_only_excludes_head_marked_unknown_and_does_not_repromote_old_pass(tmp_path: Path):
    cat = tmp_path / "catalogue.json"
    cat.write_text(json.dumps(TINY_CATALOGUE))
    data = tmp_path / "data"
    data.mkdir()
    revision = "d" * 40
    from lcstatus.evidence import Record, Store, check_fingerprint
    Store(data / "records.jsonl").add(Record(
        kind="ci_run", repo="ghost", revision=revision, verdict="pass",
        platform="linux", condition_ids=["g.c1"], executed=1, failed=0,
        source={"type": "github_actions", "check": "g.ci",
                "check_fingerprint": check_fingerprint(TINY_CATALOGUE["checks"]["g.ci"])},
        revision_time="2026-09-11T10:00:00+00:00",
    ))
    (data / "state.json").write_text(json.dumps({
        "heads": {"ghost": revision},
        "unknown_heads": ["ghost"],
        "runs": 9,
        "last_run_at": "2026-09-11T10:01:00+00:00",
        "last_failures": [{"repo": "ghost", "what": "github_head_mismatch", "why": "mirror lagged"}],
    }))

    result = subprocess.run([
        sys.executable, "-m", "lcstatus.collect", "--catalogue", str(cat),
        "--data", str(data), "--site", str(tmp_path / "site"), "--render-only",
    ], cwd=ROOT, capture_output=True, text=True, timeout=120)

    assert result.returncode == 2
    status = json.loads((tmp_path / "site" / "status.json").read_text())
    assert status["heads"] == {}
    condition = status["tasks"][0]["conditions"][0]
    assert condition["state"] == "changed_since"
    assert condition["current"] is None


@pytest.mark.parametrize("what", ["head", "git_head", "github_head", "github_head_mismatch"])
def test_legacy_failed_head_is_excluded_from_render_heads(what):
    from lcstatus.collect import render_heads

    state = {
        "heads": {"ghost": "d" * 40},
        "last_failures": [{"repo": "ghost", "what": what, "why": "unreadable"}],
    }
    assert render_heads(state) == {}


def test_legacy_non_head_failure_keeps_confirmed_render_head():
    from lcstatus.collect import render_heads

    state = {
        "heads": {"ghost": "d" * 40},
        "last_failures": [{"repo": "ghost", "what": "github_releases", "why": "unavailable"}],
    }
    assert render_heads(state) == {"ghost": "d" * 40}


def test_release_dispatch_is_scoped_to_the_configured_repository():
    from lcstatus.collect import release_targets
    from lcstatus.sources import Revision

    revisions = {
        "ew": Revision("ew", "a" * 40, "2026-09-11T00:00:00Z", "email"),
        "ip": Revision("ip", "b" * 40, "2026-09-11T00:00:00Z", "invoice"),
    }
    assert [repo for repo, _ in release_targets({"repo": "ip"}, revisions)] == ["ip"]
    assert {repo for repo, _ in release_targets({"repo": "*"}, revisions)} == {"ew", "ip"}


def test_unavailable_results_from_any_runner_are_run_failures(tmp_path: Path):
    from lcstatus.collect import store_result
    from lcstatus.evidence import Record, Store

    store = Store(tmp_path / "records.jsonl")
    failures = []
    store_result(store, failures, Record(
        kind="ci_run", repo="ghost", revision="a" * 40, verdict="unavailable",
        source={"type": "github_actions"}, summary="Actions API timed out",
    ))
    store_result(store, failures, Record(
        kind="release_artifact", repo="ghost", revision="a" * 40, verdict="unavailable",
        source={"type": "github_releases"}, summary="release lookup failed",
    ))
    store_result(store, failures, Record(
        kind="source_inspection", repo="ghost", revision="a" * 40, verdict="unavailable",
        source={"type": "source_inspection"}, summary="tree unavailable",
    ))
    store_result(store, failures, Record(
        kind="automated_test", repo="ghost", revision="a" * 40, verdict="unavailable",
        source={"type": "local_runner"}, summary="test environment unavailable",
    ))
    assert failures == [
        {"repo": "ghost", "what": "github_actions", "why": "Actions API timed out"},
        {"repo": "ghost", "what": "github_releases", "why": "release lookup failed"},
        {"repo": "ghost", "what": "source_inspection", "why": "tree unavailable"},
        {"repo": "ghost", "what": "local_runner", "why": "test environment unavailable"},
    ]


@pytest.mark.parametrize(
    ("runner_kind", "condition_kind", "source_type"),
    [
        ("source_inspection", "source_inspection", "source_inspection"),
        ("pytest", "automated_test", "local_runner"),
        ("cargo_lib", "automated_test", "local_runner"),
        ("accept_ew_ip", "automated_test", "local_runner"),
    ],
)
def test_unavailable_local_runner_path_sets_failed_exit_and_banner(
    tmp_path: Path, monkeypatch, runner_kind: str, condition_kind: str, source_type: str,
):
    import lcstatus.collect as collect
    from lcstatus.evidence import Record
    from lcstatus.sources import Revision

    check = {"runner": runner_kind, "repo": "ghost", "platform": "linux"}
    if runner_kind == "source_inspection":
        check.update({"paths": ["src/**"], "markers": ["marker"]})
    if runner_kind == "accept_ew_ip":
        check["participants"] = ["ghost"]
    catalogue = {
        "catalogue_version": 1,
        "release": {
            "target": "t", "required_platforms": ["linux", "windows"],
            "automate_scope": {"decision": "undecided", "note": "n", "required_for_first_release": None},
        },
        "repos": {"ghost": {"github": "example/ghost", "ci_workflows": []}},
        "apps": {"ghost-app": {"name": "Ghost", "repo": "ghost"}},
        "checks": {"local.check": check},
        "tasks": [{
            "id": "g.task", "app": "ghost-app", "layer": "standalone", "title": "Ghost works", "promise": "p",
            "conditions": [{"id": "g.condition", "kind": condition_kind, "check": "local.check", "proves": "proof"}],
            "depends_on": [{"repo": "ghost", "paths": ["src/**"]}],
        }],
    }
    catalogue_path = tmp_path / "catalogue.json"
    catalogue_path.write_text(json.dumps(catalogue))
    revision = Revision("ghost", "a" * 40, "2026-09-11T00:00:00+00:00", "head")

    class FakeMirrors:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, repo):
            return None

        def head(self, repo):
            return revision

    class FakeGitHub:
        def default_branch_head(self, repo):
            return {"sha": revision.sha}

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def result(self, rev, condition_ids, task_ids):
            return Record(
                kind=condition_kind, repo="ghost", revision=rev.sha, revision_time=rev.committed_at,
                verdict="unavailable", platform="linux", condition_ids=condition_ids, task_ids=task_ids,
                source={"type": source_type}, summary=f"{runner_kind} unavailable",
            )

        def source_inspection(self, check_id, check_config, rev, condition_ids, task_ids):
            return self.result(rev, condition_ids, task_ids)

        def pytest(self, check_id, check_config, rev, condition_ids, task_ids):
            return self.result(rev, condition_ids, task_ids)

        def cargo_lib(self, check_id, check_config, rev, condition_ids, task_ids):
            return self.result(rev, condition_ids, task_ids)

        def accept_ew_ip(self, check_id, check_config, revisions, condition_ids, task_ids):
            return self.result(revisions["ghost"], condition_ids, task_ids)

    monkeypatch.setattr(collect, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(collect, "Mirrors", FakeMirrors)
    monkeypatch.setattr(collect, "GitHub", FakeGitHub)
    monkeypatch.setattr(collect, "Runner", FakeRunner)
    data = tmp_path / "data"
    site = tmp_path / "site"

    rc = collect.main([
        "--catalogue", str(catalogue_path), "--data", str(data), "--site", str(site),
    ])

    status = json.loads((site / "status.json").read_text())
    assert rc == 2
    assert status["source_failures"] == [
        {"repo": "ghost", "what": source_type, "why": f"{runner_kind} unavailable"}
    ]


def test_no_fetch_and_unavailable_github_leave_cached_mirror_unknown(tmp_path: Path, monkeypatch):
    import lcstatus.collect as collect
    from lcstatus.sources import Failure, Revision

    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text(json.dumps(TINY_CATALOGUE))
    cached = Revision("ghost", "d" * 40, "2026-09-11T10:00:00+00:00", "cached")

    class FakeMirrors:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, repo):
            raise AssertionError("--no-fetch must not fetch")

        def head(self, repo):
            assert repo == "ghost"
            return cached

    class FakeGitHub:
        def default_branch_head(self, repo):
            return Failure("github", "offline")

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(collect, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(collect, "Mirrors", FakeMirrors)
    monkeypatch.setattr(collect, "GitHub", FakeGitHub)
    monkeypatch.setattr(collect, "Runner", FakeRunner)
    data = tmp_path / "data"
    site = tmp_path / "site"

    rc = collect.main([
        "--catalogue", str(catalogue), "--data", str(data), "--site", str(site),
        "--no-fetch", "--no-local",
    ])

    assert rc == 2
    status = json.loads((site / "status.json").read_text())
    state = json.loads((data / "state.json").read_text())
    assert status["heads"] == {}
    assert status["tasks"][0]["conditions"][0]["state"] != "satisfied"
    assert state["unknown_heads"] == ["ghost"]


def test_mirror_subprocess_errors_become_failures(tmp_path: Path, monkeypatch):
    from lcstatus.sources import Failure, Mirrors

    mirrors = Mirrors(tmp_path / "mirrors", {"app": {"github": "example/app"}})
    mirrors.path("app").mkdir()
    for error in (subprocess.TimeoutExpired(["git", "log"], 300), FileNotFoundError("git missing")):
        def fail_to_run(*args, **kwargs):
            raise error

        monkeypatch.setattr("lcstatus.sources.subprocess.run", fail_to_run)
        head = mirrors.head("app")
        archive = mirrors.extract("app", "a" * 40, tmp_path / f"tree-{type(error).__name__}")
        commits = mirrors.commits_between("app", "a" * 40, "b" * 40)
        assert isinstance(head, Failure) and head.what == "head"
        assert type(error).__name__ in head.why
        assert isinstance(archive, Failure) and archive.what == "archive"
        assert archive.why == type(error).__name__
        assert isinstance(commits, Failure) and commits.what == "commits"
        assert type(error).__name__ in commits.why


def test_empty_commit_range_and_failed_commit_read_stay_distinct(tmp_path: Path, monkeypatch):
    from lcstatus.sources import Failure, Mirrors

    mirrors = Mirrors(tmp_path / "mirrors", {"app": {"github": "example/app"}})
    monkeypatch.setattr(
        "lcstatus.sources._run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""),
    )
    assert mirrors.commits_between("app", "a" * 40, "b" * 40) == []
    monkeypatch.setattr(
        "lcstatus.sources._run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 124, stdout="", stderr="timed out"),
    )
    failed = mirrors.commits_between("app", "a" * 40, "b" * 40)
    assert isinstance(failed, Failure)
    assert failed.what == "commits" and failed.why == "timed out"


@pytest.mark.parametrize("failure_stage", ["diff", "commits"])
def test_failed_change_read_retries_before_advancing_baseline(tmp_path: Path, monkeypatch, failure_stage):
    import lcstatus.collect as collect
    from lcstatus.sources import Failure, Revision

    catalogue = {
        "catalogue_version": 1,
        "release": {
            "target": "t", "required_platforms": ["linux", "windows"],
            "automate_scope": {"decision": "undecided", "note": "n", "required_for_first_release": None},
        },
        "repos": {"ghost": {"github": "example/ghost", "ci_workflows": []}},
        "apps": {}, "checks": {}, "tasks": [],
    }
    catalogue_path = tmp_path / "catalogue.json"
    catalogue_path.write_text(json.dumps(catalogue))
    old, new = "a" * 40, "b" * 40
    revision = Revision("ghost", new, "2026-09-11T00:00:00+00:00", "head")
    diff_calls = []
    commit_calls = []

    class FakeMirrors:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, repo):
            return None

        def head(self, repo):
            return revision

        def changed_files(self, repo, before, after):
            diff_calls.append((before, after))
            if failure_stage == "diff" and len(diff_calls) == 1:
                return Failure("diff", "git diff timed out")
            return ["unmapped.py"]

        def commits_between(self, repo, before, after):
            commit_calls.append((before, after))
            if failure_stage == "commits" and len(commit_calls) == 1:
                return Failure("commits", "git log timed out")
            return ["abc change"]

    class FakeGitHub:
        def default_branch_head(self, repo):
            return {"sha": new}

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(collect, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(collect, "Mirrors", FakeMirrors)
    monkeypatch.setattr(collect, "GitHub", FakeGitHub)
    monkeypatch.setattr(collect, "Runner", FakeRunner)
    data = tmp_path / "data"
    data.mkdir()
    (data / "state.json").write_text(json.dumps({"heads": {"ghost": old}, "runs": 1}))
    site = tmp_path / "site"

    rc = collect.main([
        "--catalogue", str(catalogue_path), "--data", str(data), "--site", str(site), "--no-local",
    ])

    status = json.loads((site / "status.json").read_text())
    state = json.loads((data / "state.json").read_text())
    assert rc == 2
    assert status["heads"]["ghost"]["sha"] == new
    assert state["heads"]["ghost"] == new
    assert state["change_baselines"]["ghost"] == old
    assert status["source_failures"] == [{
        "repo": "ghost",
        "what": failure_stage,
        "why": f"git {failure_stage if failure_stage == 'diff' else 'log'} timed out",
    }]
    if failure_stage == "commits":
        assert status["recent_changes"][0]["commits_complete"] is False
        dashboard = (site / "dashboard.html").read_text()
        embedded = json.loads(dashboard.split("const DATA = ", 1)[1].split(";\nconst pillFor", 1)[0])
        assert embedded["recent_changes"][0]["commits_complete"] is False
        assert "commit list unavailable, retry pending" in (site / "report.md").read_text()

    render_rc = collect.main([
        "--catalogue", str(catalogue_path), "--data", str(data), "--site", str(site), "--render-only",
    ])
    rendered = json.loads((site / "status.json").read_text())
    assert render_rc == 2
    assert rendered["heads"]["ghost"]["sha"] == new
    assert json.loads((data / "state.json").read_text())["change_baselines"]["ghost"] == old

    retry_rc = collect.main([
        "--catalogue", str(catalogue_path), "--data", str(data), "--site", str(site), "--no-local",
    ])
    retried_status = json.loads((site / "status.json").read_text())
    retried_state = json.loads((data / "state.json").read_text())
    records = [json.loads(line) for line in (data / "records.jsonl").read_text().splitlines()]
    changes = [record for record in records if record["kind"] == "change"]
    assert retry_rc == 0
    assert retried_status["source_failures"] == []
    assert retried_state["heads"]["ghost"] == new
    assert retried_state["change_baselines"]["ghost"] == new
    assert len(retried_status["recent_changes"]) == 1
    assert retried_status["recent_changes"][0]["commits"] == ["abc change"]
    assert retried_status["recent_changes"][0]["commits_complete"] is True
    dashboard = (site / "dashboard.html").read_text()
    embedded = json.loads(dashboard.split("const DATA = ", 1)[1].split(";\nconst pillFor", 1)[0])
    assert embedded["recent_changes"] == retried_status["recent_changes"]
    assert "retry pending" not in (site / "report.md").read_text()
    assert diff_calls == [(old, new), (old, new)]
    assert commit_calls == [(old, new)] * (1 if failure_stage == "diff" else 2)
    assert any(change["detail"]["unmapped_files"] == ["unmapped.py"] for change in changes)
    assert any(change["detail"]["commits"] == ["abc change"] for change in changes)
    if failure_stage == "commits":
        assert any(change["detail"]["commits_complete"] is False for change in changes)
        assert len(changes) == 2


def test_set_baseline_seeds_display_head_and_change_baseline(tmp_path: Path):
    import lcstatus.collect as collect

    catalogue = {
        "catalogue_version": 1,
        "release": {
            "target": "t", "required_platforms": ["linux", "windows"],
            "automate_scope": {"decision": "undecided", "note": "n", "required_for_first_release": None},
        },
        "repos": {"ghost": {"github": "example/ghost", "ci_workflows": []}},
        "apps": {}, "checks": {}, "tasks": [],
    }
    catalogue_path = tmp_path / "catalogue.json"
    catalogue_path.write_text(json.dumps(catalogue))
    data = tmp_path / "data"
    sha = "a" * 40

    rc = collect.main([
        "--catalogue", str(catalogue_path), "--data", str(data),
        "--site", str(tmp_path / "site"), "--set-baseline", f"ghost={sha}",
    ])

    state = json.loads((data / "state.json").read_text())
    assert rc == 0
    assert state["heads"] == {"ghost": sha}
    assert state["change_baselines"] == {"ghost": sha}
