"""Contract 07: a check runs once per revision, not once per tick."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lcstatus.evidence import DECIDED_VERDICTS, Record, Store
from lcstatus.sources import Failure, Revision
from lcstatus.verify import LOCAL_RUNNERS, Runner, run_base

T0 = "2026-09-20T10:00:00+00:00"


def rev(repo, sha, when=T0):
    return Revision(repo, sha * 40 if len(sha) == 1 else sha, when, "head")


# --- B1: one definition ------------------------------------------------------------------------

def test_every_local_runner_builds_its_row_from_run_base(tmp_path: Path, monkeypatch):
    """Each runner, driven to an early `unavailable` through its real code, writes a row whose
    series identity is the planned row's -- so the collector's key cannot drift from the runner's."""
    checks = {
        "c.inspect": {"runner": "source_inspection", "repo": "ew", "paths": ["a.py"], "markers": {"m": "x"}},
        "c.pytest": {"runner": "pytest", "repo": "ew", "platform": "linux", "args": ["tests"]},
        "c.cargo": {"runner": "cargo_lib", "repo": "ds", "platform": "linux", "heavy": True},
        "c.xip": {"runner": "accept_ew_ip", "repo": "invoice-processor", "platform": "linux",
                  "participants": ["eom-email-watcher", "invoice-processor", "connect-contracts"]},
        "c.xds": {"runner": "accept_ew_ds", "repo": "eom-email-watcher", "platform": "linux", "heavy": True,
                  "participants": ["eom-email-watcher", "document-summarizer", "connect-contracts"]},
    }
    catalogue = {
        "repos": {r: {"github": f"x/{r}"} for r in ("ew", "ds", "invoice-processor", "eom-email-watcher",
                                                     "document-summarizer", "connect-contracts")},
        "checks": checks,
        "tasks": [{"id": "t", "conditions": [
            {"id": f"cond.{cid}", "kind": "source_inspection" if cid == "c.inspect" else "automated_test",
             "check": cid, "proves": f"proof for {cid}"} for cid in checks]}],
    }

    class Mirrors:
        def extract(self, repo, sha, destination):
            return Failure("git_archive", "no tree in this test")

    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    runner = Runner(Mirrors(), tmp_path / "cache", tmp_path / "logs", object(), catalogue)
    revs = {r: rev(r, "a") for r in catalogue["repos"]}
    for cid, check in checks.items():
        kind = check["runner"]
        conds, tasks = [f"cond.{cid}"], ["t"]
        if kind in ("accept_ew_ip", "accept_ew_ds"):
            row = getattr(runner, kind)(cid, check, revs, conds, tasks)
        else:
            row = getattr(runner, kind)(cid, check, revs[check["repo"]], conds, tasks)
        assert row.verdict == "unavailable", (cid, row.summary)
        planned = Record(verdict="pending", **run_base(catalogue, kind, cid, check, revs, conds, tasks))
        assert row.series_identity() == planned.series_identity(), cid
    assert set(LOCAL_RUNNERS) == {c["runner"] for c in checks.values()}


def test_store_add_and_the_collector_share_one_series_lookup(tmp_path: Path):
    store = Store(tmp_path / "records.jsonl")
    source = {"type": "local_runner", "check": "c", "check_fingerprint": "f" * 24, "condition_fingerprints": {}}
    first = Record(kind="automated_test", repo="ew", revision="a" * 40, verdict="pass",
                   revision_time=T0, source=source)
    assert store.latest_in_series(first) is None
    assert store.add(first) is True
    assert store.latest_in_series(first) is first
    assert store.add(Record(kind="automated_test", repo="ew", revision="a" * 40, verdict="pass",
                            revision_time=T0, source=dict(source))) is False
    assert DECIDED_VERDICTS == ("pass", "fail", "inconclusive")


# --- B2..B4: the collector, end to end ---------------------------------------------------------

class World:
    """One real store and site; fake mirrors, GitHub and runner.  The runner records every call and
    answers with a row built from run_base, exactly as the real runners now do."""

    def __init__(self, tmp_path: Path, monkeypatch):
        import lcstatus.collect as collect
        self.collect, self.tmp, self.monkeypatch = collect, tmp_path, monkeypatch
        self.heads = {"ghost": rev("ghost", "a"), "other": rev("other", "b")}
        self.verdict = "pass"
        self.calls: list[str] = []
        self.checks = {"g.pytest": {"runner": "pytest", "repo": "ghost", "platform": "linux", "args": ["tests"]}}
        self.conditions = [{"id": "g.c1", "kind": "automated_test", "check": "g.pytest", "proves": "it works"}]
        world = self

        class FakeMirrors:
            def __init__(self, *args, **kwargs):
                pass

            def fetch(self, repo):
                return None

            def head(self, repo):
                return world.heads[repo]

            def changed_files(self, repo, old, new):
                return []

            def commits_between(self, repo, old, new):
                return []

            def commit_time(self, repo, sha):
                return T0

        class FakeGitHub:
            def default_branch_head(self, gh_repo):
                return {"sha": world.heads[gh_repo.split("/")[-1]].sha}

        class FakeRunner:
            def __init__(self, *args, **kwargs):
                self.cat = args[-1]

            def _row(self, kind, cid, check, revisions, conds, tasks):
                world.calls.append(cid)
                verdict = "inconclusive" if kind == "source_inspection" else world.verdict
                return Record(verdict=verdict, **run_base(self.cat, kind, cid, check, revisions, conds, tasks))

            def source_inspection(self, cid, check, r, conds, tasks):
                return self._row("source_inspection", cid, check, {check["repo"]: r}, conds, tasks)

            def pytest(self, cid, check, r, conds, tasks):
                return self._row("pytest", cid, check, {check["repo"]: r}, conds, tasks)

            def accept_ew_ip(self, cid, check, revisions, conds, tasks):
                return self._row("accept_ew_ip", cid, check, revisions, conds, tasks)

        monkeypatch.setattr(collect, "CACHE", tmp_path / "cache")
        monkeypatch.setattr(collect, "Mirrors", FakeMirrors)
        monkeypatch.setattr(collect, "GitHub", FakeGitHub)
        monkeypatch.setattr(collect, "Runner", FakeRunner)

    def run(self, *extra: str) -> list[str]:
        catalogue = {
            "release": {"required_platforms": ["linux"], "target": "t",
                        "automate_scope": {"decision": "undecided", "note": "n", "required_for_first_release": None}},
            "repos": {"ghost": {"github": "x/ghost", "ci_workflows": []}, "other": {"github": "x/other", "ci_workflows": []}},
            "apps": {"app": {"name": "App", "repo": "ghost"}},
            "checks": self.checks,
            "tasks": [{"id": "g.task", "app": "app", "layer": "standalone", "title": "T", "promise": "p",
                       "conditions": self.conditions, "depends_on": []}],
        }
        path = self.tmp / "catalogue.json"
        path.write_text(json.dumps(catalogue))
        self.calls = []
        self.collect.main(["--catalogue", str(path), "--data", str(self.tmp / "data"),
                           "--site", str(self.tmp / "site"), *extra])
        return self.calls

    def rows(self) -> int:
        return len([l for l in (self.tmp / "data" / "records.jsonl").read_text().splitlines() if l.strip()])


@pytest.mark.parametrize("verdict", ["pass", "fail"])
def test_decided_result_at_the_same_revision_is_not_rerun(tmp_path: Path, monkeypatch, capsys, verdict):
    world = World(tmp_path, monkeypatch)
    world.verdict = verdict
    assert world.run() == ["g.pytest"]                 # first sight of this revision: it runs
    before = world.rows()
    assert world.run() == []                           # same revision, decided: it does not
    assert world.rows() == before
    assert f"unchanged g.pytest @ {'a' * 12}: {verdict}" in capsys.readouterr().out


def test_an_inspection_reading_is_decided(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    world.checks = {"g.inspect": {"runner": "source_inspection", "repo": "ghost", "paths": ["a.py"], "markers": {"m": "x"}}}
    world.conditions = [{"id": "g.i1", "kind": "source_inspection", "check": "g.inspect", "proves": "points at code"}]
    assert world.run() == ["g.inspect"]
    assert world.run() == []


@pytest.mark.parametrize("verdict", ["unavailable", "skip", "unknown"])
def test_non_decisive_result_is_retried(tmp_path: Path, monkeypatch, verdict):
    world = World(tmp_path, monkeypatch)
    world.verdict = verdict
    assert world.run() == ["g.pytest"]
    assert world.run() == ["g.pytest"]                 # a harness answer is not an answer: retry
    world.verdict = "pass"
    assert world.run() == ["g.pytest"]                 # ...until it decides
    assert world.run() == []


def test_new_revision_or_new_configuration_runs(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    assert world.run() == ["g.pytest"]
    assert world.run() == []
    world.heads["ghost"] = rev("ghost", "c")           # the code moved
    assert world.run() == ["g.pytest"]
    assert world.run() == []
    world.checks["g.pytest"]["args"] = ["tests", "-k", "fast"]   # the check's configuration changed
    assert world.run() == ["g.pytest"]
    world.conditions[0]["proves"] = "it works, reworded"         # the claim changed
    assert world.run() == ["g.pytest"]
    world.conditions.append({"id": "g.c2", "kind": "automated_test", "check": "g.pytest", "proves": "new"})
    assert world.run() == ["g.pytest"]                           # a condition was added to the check
    assert world.run() == []
    world.checks["g.pytest"]["note"] = "prose only"              # a note is not configuration
    assert world.run() == []


def test_cross_app_key_uses_every_declared_participant(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    world.checks = {"g.xapp": {"runner": "accept_ew_ip", "repo": "ghost", "platform": "linux",
                               "participants": ["ghost", "other"]}}
    world.conditions = [{"id": "g.x1", "kind": "automated_test", "check": "g.xapp", "proves": "they work together"}]
    assert world.run() == ["g.xapp"]
    assert world.run() == []
    world.heads["other"] = rev("other", "d")           # only the other participant moved
    assert world.run() == ["g.xapp"]
    assert world.run() == []


def test_rerun_and_checks_force_execution(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    assert world.run() == ["g.pytest"]
    assert world.run() == []
    assert world.run("--rerun") == ["g.pytest"]
    assert world.run("--checks", "g.pytest") == ["g.pytest"]
    assert world.run() == []


def test_cheap_reads_still_run_every_tick(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    assert world.run() == ["g.pytest"]
    before = json.loads((tmp_path / "data" / "state.json").read_text())["runs"]
    world.run()
    state = json.loads((tmp_path / "data" / "state.json").read_text())
    assert state["runs"] == before + 1                 # the tick itself still happened
    assert state["heads"]["ghost"] == "a" * 40         # heads are still observed
    assert (tmp_path / "site" / "status.json").exists()
