"""Contract 08: every dependency pattern names code that exists, checked against real git trees."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import lcstatus.collect as collect
from lcstatus import catalogue as catmod
from lcstatus.change import assess, uncovered_patterns
from lcstatus.evidence import collection_lock
from lcstatus.render import dashboard_html, report_md
from lcstatus.sources import Failure, Mirrors

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_AUTHOR_DATE": "2026-09-01T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-09-01T00:00:00+00:00",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def make_repo(tmp: Path, repo: str, files: dict[str, str]) -> Path:
    """A real working repository with one commit on main."""
    work = tmp / "work" / repo
    work.mkdir(parents=True)
    git(work, "init", "--quiet", "-b", "main")
    for name, text in files.items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", "first")
    return work


def mirror(work: Path, mirrors: Path, repo: str) -> str:
    """Clone it the way the collector owns its mirrors, and return the head."""
    mirrors.mkdir(parents=True, exist_ok=True)
    git(mirrors, "clone", "--quiet", "--mirror", str(work), f"{repo}.git")
    return git(work, "rev-parse", "HEAD")


def catalogue(depends_on: list[dict], repos: tuple[str, ...] = ("ghost",)) -> dict:
    return {
        "release": {"required_platforms": ["linux"], "target": "t",
                    "automate_scope": {"decision": "undecided", "note": "n", "required_for_first_release": None}},
        "repos": {repo: {"github": f"x/{repo}", "ci_workflows": []} for repo in repos},
        "apps": {"app": {"name": "App", "repo": repos[0]}},
        "checks": {},
        "tasks": [{"id": "g.task", "app": "app", "layer": "standalone", "title": "T", "promise": "p",
                   "conditions": [], "depends_on": depends_on}],
    }


# ---------------------------------------------------------------------------------------- B2

@pytest.mark.parametrize("depends_on, message", [
    ({"repo": "ghost", "paths": ["src/**"]}, "depends_on must be a list"),
    (["src/**"], "each depends_on item must be an object"),
    ([{"repo": "elsewhere", "paths": ["src/**"]}], "must name one catalogue repository, got 'elsewhere'"),
    ([{"paths": ["src/**"]}], "must name one catalogue repository, got None"),
    ([{"repo": "ghost", "paths": []}], "non-empty list of distinct non-empty strings"),
    ([{"repo": "ghost", "paths": "src/**"}], "non-empty list of distinct non-empty strings"),
    ([{"repo": "ghost"}], "non-empty list of distinct non-empty strings"),
    ([{"repo": "ghost", "paths": ["src/**", " "]}], "non-empty list of distinct non-empty strings"),
    ([{"repo": "ghost", "paths": ["src/**", 7]}], "non-empty list of distinct non-empty strings"),
    ([{"repo": "ghost", "paths": ["src/**", "src/**"]}], "non-empty list of distinct non-empty strings"),
    ([{"repo": "ghost", "paths": ["src/**"]}, {"repo": "ghost", "paths": ["a.py"]}], "names ghost twice"),
])
def test_catalogue_rejects_malformed_depends_on(tmp_path: Path, depends_on, message):
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(catalogue(depends_on)))
    with pytest.raises(ValueError) as caught:
        catmod.load(path)
    assert "task g.task: " in str(caught.value)
    assert message in str(caught.value)


def test_well_formed_depends_on_and_the_live_catalogue_load(tmp_path: Path):
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(catalogue([{"repo": "ghost", "paths": ["src/**", "a.py"]}])))
    catmod.load(path)
    catmod.load(Path(__file__).resolve().parent.parent / "catalogue.json")


# ---------------------------------------------------------------------------------------- B3

def test_pattern_coverage_uses_assess_matcher():
    files = ["src/app.py", "src/a/b/x.py", "tauri.dev.conf.json", "exact.txt", "srcx/y.py", "docs"]
    patterns = ["src/**", "docs/**", "src/a/**/x.py", "src/z/**/x.py", "tauri*.conf.json",
                "exact.txt", "missing.txt", "src", "srcx/**", "sr?x/y.py", "docs"]
    tasks = [{"id": f"t{i}", "depends_on": [{"repo": "r", "paths": [p]}]} for i, p in enumerate(patterns)]
    matched = set(assess("r", "a", "b", files, tasks).affected_tasks)
    dead = {task for task, _ in uncovered_patterns("r", files, tasks)}
    # every pattern is exactly one of: attributed by assess, or reported dead
    assert matched.isdisjoint(dead)
    assert matched | dead == {t["id"] for t in tasks}
    # a file named "docs" satisfies "docs/**" in both, by the same equality rule
    assert {patterns[int(t[1:])] for t in dead} == {"src/z/**/x.py", "missing.txt", "src"}


def test_uncovered_patterns_only_reads_the_named_repository():
    tasks = [{"id": "t", "depends_on": [{"repo": "r", "paths": ["a.py"]}, {"repo": "q", "paths": ["gone.py"]}]}]
    assert uncovered_patterns("r", ["a.py"], tasks) == []
    assert uncovered_patterns("q", ["a.py"], tasks) == [("t", "gone.py")]


def test_mapping_check_gaps_and_unchecked(tmp_path: Path):
    mirrors_root = tmp_path / "mirrors"
    head = mirror(make_repo(tmp_path, "ghost", {"src/app.py": "", "odd name.txt": ""}), mirrors_root, "ghost")
    broken = mirror(make_repo(tmp_path, "broken", {"a.py": ""}), mirrors_root, "broken")
    cat = catalogue(
        [{"repo": "ghost", "paths": ["src/**", "odd name.txt", "gone.py"]},
         {"repo": "unseen", "paths": ["a.py"]},
         {"repo": "broken", "paths": ["a.py"]}],
        repos=("ghost", "unseen", "broken", "unnamed"),
    )
    mirrors = Mirrors(mirrors_root, cat["repos"])
    heads = {"ghost": head, "broken": "f" * 40, "unnamed": head}   # "broken" at a SHA its mirror lacks
    assert broken != "f" * 40
    result = collect.mapping_check(cat, heads, mirrors)
    assert result["gaps"] == [{"task": "g.task", "repo": "ghost", "pattern": "gone.py", "revision": head}]
    unchecked = {u["repo"]: u["why"] for u in result["unchecked"]}
    assert unchecked["unseen"] == "current revision not observed this tick"
    assert unchecked["broken"].startswith("file listing failed: ")
    assert set(unchecked) == {"unseen", "broken"}       # a repository no task names is not reported


def test_a_listing_that_fails_is_a_failure_never_an_empty_tree(tmp_path: Path):
    mirrors_root = tmp_path / "mirrors"
    head = mirror(make_repo(tmp_path, "ghost", {"a.py": ""}), mirrors_root, "ghost")
    mirrors = Mirrors(mirrors_root, {"ghost": {}})
    assert mirrors.files_at("ghost", head) == ["a.py"]
    assert isinstance(mirrors.files_at("ghost", "f" * 40), Failure)
    assert isinstance(mirrors.files_at("absent", head), Failure)


# ------------------------------------------------------------------------------ B3/B4 in a tick

class FakeGitHub:
    """GitHub is the one external source here; it agrees with the mirror's head."""

    def __init__(self, work: dict[str, Path]):
        self.work = work

    def default_branch_head(self, gh_repo: str):
        return {"sha": git(self.work[gh_repo.split("/")[-1]], "rev-parse", "HEAD")}


def run_tick(tmp: Path, cat: dict, *extra: str) -> int:
    path = tmp / "catalogue.json"
    path.write_text(json.dumps(cat))
    return collect.main(["--catalogue", str(path), "--data", str(tmp / "data"), "--site", str(tmp / "site"), *extra])


def test_a_moved_file_shows_as_a_gap_on_the_page_and_nowhere_else(tmp_path: Path, monkeypatch):
    work = make_repo(tmp_path, "ghost", {"src/app.py": "", "keep.py": ""})
    mirror(work, tmp_path / "cache" / "mirrors", "ghost")
    monkeypatch.setattr(collect, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(collect, "GitHub", lambda: FakeGitHub({"ghost": work}))
    cat = catalogue([{"repo": "ghost", "paths": ["src/**", "keep.py"]}])

    assert run_tick(tmp_path, cat) == 0
    state = json.loads((tmp_path / "data" / "state.json").read_text())
    assert state["mapping"] == {"gaps": [], "unchecked": []}
    assert json.loads((tmp_path / "site" / "status.json").read_text())["catalogue_mapping"] == state["mapping"]
    assert "Catalogue mapping" not in (tmp_path / "site" / "report.md").read_text()

    # the product repository moves the file the catalogue names; the real mirror fetches it
    git(work, "mv", "keep.py", "moved.py")
    git(work, "commit", "--quiet", "-m", "move")
    head = git(work, "rev-parse", "HEAD")
    assert run_tick(tmp_path, cat) == 0                   # a gap is not a source failure (D2)
    state = json.loads((tmp_path / "data" / "state.json").read_text())
    gap = {"task": "g.task", "repo": "ghost", "pattern": "keep.py", "revision": head}
    assert state["mapping"] == {"gaps": [gap], "unchecked": []}
    assert state["last_failures"] == []
    page = json.loads((tmp_path / "site" / "status.json").read_text())
    assert page["catalogue_mapping"]["gaps"] == [gap]
    assert page["source_failures"] == []
    report = (tmp_path / "site" / "report.md").read_text()
    assert "**Catalogue mapping: 1 dependency pattern(s) match no file at the current revision.**" in report
    assert "> - g.task — ghost: `keep.py`" in report
    # the change itself names what moved and, since no pattern claims moved.py, leaves it unmapped
    records = [json.loads(l) for l in (tmp_path / "data" / "records.jsonl").read_text().splitlines()]
    change = [r for r in records if r["kind"] == "change"][-1]
    assert change["detail"]["unmapped_files"] == ["moved.py"]

    # --render-only re-renders the same mapping from state, without reading any mirror
    class NoMirrorReads:
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            pytest.fail(f"--render-only called Mirrors.{name}")

    (tmp_path / "site" / "status.json").unlink()
    monkeypatch.setattr(collect, "Mirrors", NoMirrorReads)
    assert run_tick(tmp_path, cat, "--render-only") == 0
    assert json.loads((tmp_path / "site" / "status.json").read_text())["catalogue_mapping"]["gaps"] == [gap]


def test_the_mapping_banner_text_and_its_absence():
    payload = {
        "generated_at": None, "collection_run": 1, "heads": {}, "source_failures": [], "recent_changes": [],
        "tasks": [], "release": {"target": "t", "required_platforms": ["linux"],
                                 "automate_scope": {"decision": "undecided", "note": "n"}},
        "catalogue_mapping": {"gaps": [{"task": "g.task", "repo": "ghost", "pattern": "keep.py", "revision": "a"}],
                              "unchecked": [{"repo": "quiet", "why": "current revision not observed this tick"}]},
    }
    report = report_md(payload, {"release": payload["release"]})
    assert "**Catalogue mapping not checked this run for:** quiet (current revision not observed this tick)" in report
    dashboard = dashboard_html(payload, {"release": payload["release"]})
    assert "dependency pattern(s) match no file at the current revision.</b>" in dashboard
    assert "Catalogue mapping not checked this run for:" in dashboard
    for absent in (None, {"gaps": [], "unchecked": []}):
        payload["catalogue_mapping"] = absent
        assert "Catalogue mapping" not in report_md(payload, {"release": payload["release"]})


# ---------------------------------------------------------------------------------------- B5

def test_the_collector_and_the_helper_share_one_lock(tmp_path: Path, capsys):
    held = collection_lock(tmp_path / "data", wait=False)
    try:
        assert held is not None
        assert collection_lock(tmp_path / "data", wait=False) is None
        # a routine tick yields to whoever holds the helper's lock, before it reads anything
        assert collect.main(["--catalogue", str(tmp_path / "absent.json"), "--data", str(tmp_path / "data"),
                             "--site", str(tmp_path / "site")]) == 3
        assert "another collection is running" in capsys.readouterr().err
    finally:
        held.close()
    reacquired = collection_lock(tmp_path / "data", wait=False)
    assert reacquired is not None
    reacquired.close()
