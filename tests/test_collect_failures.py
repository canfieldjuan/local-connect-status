"""Source failures must be loud. A repo that cannot be read, or a GitHub that cannot be asked,
produces explicit failure records and a non-zero exit — never a clean, empty, green report."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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
    from lcstatus.evidence import Record, Store
    Store(data / "records.jsonl").add(Record(
        kind="ci_run", repo="ghost", revision=revision, verdict="pass",
        platform="linux", condition_ids=["g.c1"], executed=1, failed=0,
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


def test_legacy_failed_head_is_excluded_from_render_heads():
    from lcstatus.collect import render_heads

    state = {
        "heads": {"ghost": "d" * 40},
        "last_failures": [{"repo": "ghost", "what": "git_head", "why": "unreadable"}],
    }
    assert render_heads(state) == {}


def test_release_dispatch_is_scoped_to_the_configured_repository():
    from lcstatus.collect import release_targets
    from lcstatus.sources import Revision

    revisions = {
        "ew": Revision("ew", "a" * 40, "2026-09-11T00:00:00Z", "email"),
        "ip": Revision("ip", "b" * 40, "2026-09-11T00:00:00Z", "invoice"),
    }
    assert [repo for repo, _ in release_targets({"repo": "ip"}, revisions)] == ["ip"]
    assert {repo for repo, _ in release_targets({"repo": "*"}, revisions)} == {"ew", "ip"}


def test_unavailable_actions_and_release_results_are_run_failures(tmp_path: Path):
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
    assert failures == [
        {"repo": "ghost", "what": "github_actions", "why": "Actions API timed out"},
        {"repo": "ghost", "what": "github_releases", "why": "release lookup failed"},
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
