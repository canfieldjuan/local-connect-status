"""Contract 07: a check runs once per revision, not once per tick."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ast

import lcstatus.verify as verify
from lcstatus.evidence import Record, Store
from lcstatus.sources import Failure, Revision
from lcstatus.verify import LOCAL_RUNNERS, Runner, decided, run_base

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


def test_every_runner_return_is_built_from_base():
    """Every Record(...) in the five local runners spreads **base, and nothing edits base after it
    is built -- so no return path can make its row a different series from the planned row."""
    tree = ast.parse(Path(verify.__file__).read_text())
    runner_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Runner")
    methods = {n.name: n for n in runner_class.body if isinstance(n, ast.FunctionDef)}
    for name in LOCAL_RUNNERS:
        fn = methods[name]
        records = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "Record"]
        assert records, name
        for call in records:
            spreads = [k for k in call.keywords if k.arg is None]
            assert any(isinstance(k.value, ast.Name) and k.value.id == "base" for k in spreads), (name, call.lineno)
        writes = [n for n in ast.walk(fn)
                  if (isinstance(n, (ast.Assign, ast.AugAssign)) and any(
                      isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "base"
                      for t in (n.targets if isinstance(n, ast.Assign) else [n.target])))
                  or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                      and isinstance(n.func.value, ast.Name) and n.func.value.id == "base")]
        assert writes == [], (name, [w.lineno for w in writes])
        assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "base" for t in n.targets)]
        assert len(assigns) == 1, (name, [a.lineno for a in assigns])


def test_decided_requires_positive_proof():
    def row(verdict, failed=None):
        return Record(kind="automated_test", repo="ew", revision="a" * 40, verdict=verdict, failed=failed)
    for runner in LOCAL_RUNNERS:
        assert decided(runner, row("pass")) is True
        for verdict in ("skip", "unknown", "unavailable", "pending", "partial"):
            assert decided(runner, row(verdict)) is False, (runner, verdict)
        assert decided(runner, row("fail")) is False, runner                 # no count: may be the harness
        assert decided(runner, row("fail", failed=0)) is False, runner      # e.g. pytest exit 4/5
    for runner in ("pytest", "cargo_lib"):                                   # the framework counted a failure
        assert decided(runner, row("fail", failed=1)) is True
        assert decided(runner, row("fail", failed=True)) is False           # not a count
    for runner in ("accept_ew_ip", "accept_ew_ds"):                          # exit-code derived, never a count
        assert decided(runner, row("fail", failed=1)) is False
    assert decided("source_inspection", row("inconclusive")) is True
    assert decided("pytest", row("inconclusive")) is False


# --- B2..B4: the collector, end to end ---------------------------------------------------------

class World:
    """One real store and site; fake mirrors, GitHub and runner.  The runner records every call and
    answers with a row built from run_base, exactly as the real runners now do."""

    def __init__(self, tmp_path: Path, monkeypatch):
        import lcstatus.collect as collect
        self.collect, self.tmp, self.monkeypatch = collect, tmp_path, monkeypatch
        self.heads = {"ghost": rev("ghost", "a"), "other": rev("other", "b")}
        self.verdict = "pass"
        self.failed: int | None = 1
        self.pins: dict[str, str] = {}
        self.broken: set[str] = set()
        self.reads: list[str] = []
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
                if repo in world.broken:
                    return Failure("head", "unreadable in this test")
                return world.heads[repo]

            def changed_files(self, repo, old, new):
                return []

            def commits_between(self, repo, old, new):
                return []

            def commit_time(self, repo, sha):
                return T0

        class FakeGitHub:
            def default_branch_head(self, gh_repo):
                repo = gh_repo.split("/")[-1]
                if repo in world.broken:
                    return Failure("github", "unreadable in this test")
                return {"sha": world.heads[repo].sha}

        class FakeRunner:
            def __init__(self, *args, **kwargs):
                self.cat = args[-1]

            def _row(self, kind, cid, check, revisions, conds, tasks):
                world.calls.append(cid)
                verdict = "inconclusive" if kind == "source_inspection" else world.verdict
                failed = world.failed if verdict == "fail" else None
                return Record(verdict=verdict, failed=failed,
                              **run_base(self.cat, kind, cid, check, revisions, conds, tasks))

            def source_inspection(self, cid, check, r, conds, tasks):
                return self._row("source_inspection", cid, check, {check["repo"]: r}, conds, tasks)

            def pytest(self, cid, check, r, conds, tasks):
                return self._row("pytest", cid, check, {check["repo"]: r}, conds, tasks)

            def cargo_lib(self, cid, check, r, conds, tasks):
                return self._row("cargo_lib", cid, check, {check["repo"]: r}, conds, tasks)

            def accept_ew_ip(self, cid, check, revisions, conds, tasks):
                return self._row("accept_ew_ip", cid, check, revisions, conds, tasks)

            def accept_ew_ds(self, cid, check, revisions, conds, tasks):
                return self._row("accept_ew_ds", cid, check, revisions, conds, tasks)

            def ci_jobs(self, repo, r, checks, cond_map):
                world.reads.append(f"ci:{repo}")
                return []

            def releases(self, cid, check, repo, r, conds, tasks):
                world.reads.append(f"release:{cid}")
                return Record(kind="release_artifact", repo=repo, revision=r.sha, revision_time=r.committed_at,
                              verdict="fail", summary="no published release", condition_ids=conds,
                              source={"type": "github_release", "check": cid})

            def release_issues(self, cid, check, r, conds, tasks):
                world.reads.append(f"issues:{cid}")
                return Record(kind="issue_gate", repo=check["repo"], revision=r.sha, revision_time=r.committed_at,
                              verdict="pass", summary="0 open issues", condition_ids=conds,
                              source={"type": "github_issues", "check": cid})

        monkeypatch.setattr(collect, "CACHE", tmp_path / "cache")
        monkeypatch.setattr(collect, "Mirrors", FakeMirrors)
        monkeypatch.setattr(collect, "GitHub", FakeGitHub)
        monkeypatch.setattr(collect, "Runner", FakeRunner)

    def run(self, *extra: str) -> list[str]:
        catalogue = {
            "release": {"required_platforms": ["linux"], "target": "t",
                        "automate_scope": {"decision": "undecided", "note": "n", "required_for_first_release": None}},
            "repos": {repo: {"github": f"x/{repo}", "ci_workflows": [],
                             **({"python": self.pins[repo]} if repo in self.pins else {})}
                      for repo in ("ghost", "other")},
            "apps": {"app": {"name": "App", "repo": "ghost"}},
            "checks": self.checks,
            "tasks": [{"id": "g.task", "app": "app", "layer": "standalone", "title": "T", "promise": "p",
                       "conditions": self.conditions, "depends_on": []}],
        }
        path = self.tmp / "catalogue.json"
        path.write_text(json.dumps(catalogue))
        self.calls, self.reads = [], []
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


@pytest.mark.parametrize("verdict", ["unavailable", "skip", "unknown", "uncounted fail"])
def test_non_decisive_result_is_retried(tmp_path: Path, monkeypatch, verdict):
    world = World(tmp_path, monkeypatch)
    world.verdict, world.failed = ("fail", None) if verdict == "uncounted fail" else (verdict, 1)
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


def test_the_tick_still_observes_heads_and_renders(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    assert world.run() == ["g.pytest"]
    before = json.loads((tmp_path / "data" / "state.json").read_text())["runs"]
    world.run()
    state = json.loads((tmp_path / "data" / "state.json").read_text())
    assert state["runs"] == before + 1                 # the tick itself still happened
    assert state["heads"]["ghost"] == "a" * 40         # heads are still observed
    assert (tmp_path / "site" / "status.json").exists()


def test_every_input_that_produces_a_result_is_in_the_key(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    monkeypatch.setattr(verify, "collector_fingerprint", lambda: world.collector)
    world.collector = "c" * 24
    assert world.run() == ["g.pytest"]
    assert world.run() == []
    world.pins["ghost"] = "3.14"                          # the interpreter pin (repos[...].python)
    assert world.run() == ["g.pytest"]
    assert world.run() == []
    world.pins["other"] = "3.14"                          # a repository this run does not touch
    assert world.run() == []
    world.collector = "d" * 24                            # the collector's own code changed
    assert world.run() == ["g.pytest"]
    assert world.run() == []
    world.failed, world.verdict = 2, "fail"               # a counted failure is decided too
    world.collector = "e" * 24
    assert world.run() == ["g.pytest"]
    assert world.run() == []


def test_the_real_collector_fingerprint_covers_the_whole_package():
    verify.collector_fingerprint.cache_clear()
    first = verify.collector_fingerprint()
    assert len(first) == 24 and first == verify.collector_fingerprint()
    package = sorted(p.name for p in Path(verify.__file__).parent.glob("*.py"))
    assert {"verify.py", "collect.py", "evidence.py", "rules.py", "sources.py"} <= set(package)


def test_heavy_checks_obey_the_skip(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    world.checks = {"g.cargo": {"runner": "cargo_lib", "repo": "ghost", "platform": "linux", "heavy": True}}
    world.conditions = [{"id": "g.k1", "kind": "automated_test", "check": "g.cargo", "proves": "the library works"}]
    assert world.run() == []                              # routine tick: heavy checks are not selected
    assert world.run("--heavy") == ["g.cargo"]
    assert world.run("--heavy") == []                     # heavy, decided at this revision: skipped


def test_unobserved_participant_head_launches_nothing(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    world.checks = {"g.xapp": {"runner": "accept_ew_ds", "repo": "ghost", "platform": "linux",
                               "participants": ["ghost", "other"]}}
    world.conditions = [{"id": "g.x1", "kind": "automated_test", "check": "g.xapp", "proves": "they work together"}]
    world.broken = {"other"}
    assert world.run() == []                              # no head for `other`: nothing to run against
    world.broken = set()
    assert world.run() == ["g.xapp"]


def test_removing_a_condition_runs(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    world.conditions.append({"id": "g.c2", "kind": "automated_test", "check": "g.pytest", "proves": "more"})
    assert world.run() == ["g.pytest"]
    assert world.run() == []
    world.conditions.pop()
    assert world.run() == ["g.pytest"]


def test_reads_still_run_when_local_checks_are_decided(tmp_path: Path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    world.checks.update({
        "g.ci": {"runner": "ci_job", "repo": "ghost", "workflow": "CI", "job": "test", "platform": "linux"},
        "g.rel": {"runner": "github_release", "repo": "ghost"},
        "g.gate": {"runner": "github_issues", "repo": "ghost", "platform": "n/a", "milestone": "First Public Release"},
    })
    assert world.run() == ["g.pytest"]
    assert sorted(world.reads) == ["ci:ghost", "issues:g.gate", "release:g.rel"]
    assert world.run() == []                              # the local check is decided...
    assert sorted(world.reads) == ["ci:ghost", "issues:g.gate", "release:g.rel"]   # ...the reads are not

