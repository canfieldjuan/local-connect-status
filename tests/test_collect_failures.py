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
