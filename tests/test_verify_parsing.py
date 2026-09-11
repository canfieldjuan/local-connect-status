"""Runner verdicts come from exit codes and counted results, not from what a process printed."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lcstatus.evidence import Record


def test_junit_counts_and_exit_code_decide(tmp_path: Path):
    # A tiny "suite" that prints a passing line but exits 1: the collector's rule is exit first.
    junit = tmp_path / "j.xml"
    junit.write_text('<testsuites><testsuite tests="3" failures="0" errors="0" skipped="1"/></testsuites>')
    import xml.etree.ElementTree as ET
    root = ET.parse(junit).getroot()
    suites = root.findall(".//testsuite") or [root]
    tests = sum(int(s.get("tests", 0)) for s in suites)
    skips = sum(int(s.get("skipped", 0)) for s in suites)
    assert (tests - skips, skips) == (2, 1)
    r = Record(kind="automated_test", repo="ip", revision="c" * 40, verdict="fail", executed=2, skipped=1, failed=0,
               exit_code=1, summary="3 passed in 0.01s")
    assert r.verdict == "fail"


def test_a_process_can_print_passed_and_still_fail():
    code = textwrap.dedent("""
        print("5 passed in 0.10s")
        raise SystemExit(1)
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert "passed" in r.stdout and r.returncode == 1



def test_find_tool_probes_nvm_and_pnpm_homes_when_path_lacks_it(tmp_path, monkeypatch):
    from lcstatus.verify import find_tool
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert find_tool("pnpm", home=tmp_path) is None
    b = tmp_path / ".nvm/versions/node/v20.20.0/bin"; b.mkdir(parents=True)
    exe = b / "pnpm"; exe.write_text("#!/bin/sh\n"); exe.chmod(0o755)
    assert find_tool("pnpm", home=tmp_path) == str(exe)


def test_uv_sync_is_pinned_to_the_catalogue_interpreter_when_declared():
    from lcstatus.verify import uv_sync_command
    assert uv_sync_command("3.13")[-2:] == ["--python", "3.13"]
    assert "--python" not in uv_sync_command(None)


def test_release_verdict_requires_a_published_release_with_every_required_asset():
    from lcstatus.verify import release_verdict
    req = {"windows installer": r"\.(exe|msi)$", "linux package": r"\.(deb|AppImage)$", "checksums": r"(SHA256SUMS|\.sha256)$"}
    assert release_verdict([], req)[0] == "fail"
    assert release_verdict([{"tag_name": "v1", "draft": True, "assets": []}], req)[0] == "fail"
    assert release_verdict([{"tag_name": "v1", "prerelease": True, "assets": []}], req)[0] == "fail"
    v, summary, detail, tag = release_verdict([{"tag_name": "v1", "assets": [
        {"name": "app-1.0.deb", "state": "uploaded", "size": 10},
    ]}], req)
    assert v == "fail" and "windows installer" in summary and "checksums" in summary and tag == "v1"
    full = [{"tag_name": "v1", "published_at": "2026-09-11T00:00:00Z",
             "assets": [
                 {"id": 1, "name": "app-setup.exe", "state": "uploaded", "size": 100,
                  "digest": "sha256:" + "a" * 64},
                 {"id": 2, "name": "app_1.0_amd64.deb", "state": "uploaded", "size": 200,
                  "digest": "sha256:" + "b" * 64},
                 {"id": 3, "name": "SHA256SUMS", "state": "uploaded", "size": 300},
             ]}]
    sums = "a" * 64 + "  app-setup.exe\n" + "b" * 64 + "  app_1.0_amd64.deb\n"
    v, summary, detail, tag = release_verdict(full, req, {"3": sums})
    assert v == "pass" and detail["missing"] == [] and tag == "v1"

    for broken_assets, checksum_contents in (
        ([
            {"id": 1, "name": "app-setup.exe", "state": "uploaded", "size": 0},
            {"id": 2, "name": "app_1.0_amd64.deb", "state": "uploaded", "size": 200},
            {"id": 3, "name": "SHA256SUMS", "state": "uploaded", "size": 300},
        ], {"3": sums}),
        ([
            {"id": 1, "name": "app-setup.exe", "state": "new", "size": 100},
            {"id": 2, "name": "app_1.0_amd64.deb", "state": "uploaded", "size": 200},
            {"id": 3, "name": "SHA256SUMS", "state": "uploaded", "size": 300},
        ], {"3": sums}),
        (full[0]["assets"], {"3": "c" * 64 + "  unrelated.txt\n"}),
        (full[0]["assets"], {"3": "0" * 64 + "  app-setup.exe\n" +
                                   "1" * 64 + "  app_1.0_amd64.deb\n"}),
        ([dict(full[0]["assets"][0], digest=None), *full[0]["assets"][1:]], {"3": sums}),
    ):
        broken = [{"tag_name": "v1", "assets": broken_assets}]
        assert release_verdict(broken, req, checksum_contents)[0] == "fail"



def test_latest_distinct_workflow_run_beats_older_rerun_attempt():
    from lcstatus.verify import latest_workflow_runs

    runs = [
        {"name": "CI", "id": 100, "run_number": 20, "run_attempt": 2,
         "created_at": "2026-09-10T10:00:00Z"},
        {"name": "CI", "id": 101, "run_number": 21, "run_attempt": 1,
         "created_at": "2026-09-11T10:00:00Z"},
    ]
    assert latest_workflow_runs(runs)["CI"]["id"] == 101


def test_prepare_owned_symlink_refuses_real_directory_without_deleting_it(tmp_path: Path):
    from lcstatus.verify import prepare_owned_symlink

    link = tmp_path / "watcher-main"
    link.mkdir()
    sentinel = link / "keep.txt"
    sentinel.write_text("keep")
    owned = tmp_path / "owned"
    owned.mkdir()
    target = owned / "tree"
    target.mkdir()

    failure = prepare_owned_symlink(link, target, owned)
    assert failure is not None and failure.what == "compatibility_path"
    assert sentinel.read_text() == "keep"
    assert not link.is_symlink()


def test_prepare_owned_symlink_replaces_only_a_link_into_owned_tree(tmp_path: Path):
    from lcstatus.verify import prepare_owned_symlink

    owned = tmp_path / "owned"
    old_target = owned / "old"
    new_target = owned / "new"
    old_target.mkdir(parents=True)
    new_target.mkdir()
    link = tmp_path / "watcher-main"
    link.symlink_to(old_target, target_is_directory=True)

    assert prepare_owned_symlink(link, new_target, owned) is None
    assert link.is_symlink() and link.resolve() == new_target.resolve()


def test_uv_sync_timeout_becomes_an_explicit_failure(tmp_path: Path, monkeypatch):
    from lcstatus.sources import Failure
    from lcstatus.verify import Runner

    class Mirrors:
        pass

    class GitHub:
        pass

    runner = Runner(Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(), {"repos": {"ip": {}}})
    tree = tmp_path / "tree"
    tree.mkdir()
    monkeypatch.setattr("lcstatus.verify.shutil.which", lambda name: "/usr/bin/uv")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1200)

    monkeypatch.setattr("lcstatus.verify.subprocess.run", timeout)
    result = runner._venv("ip", tree, "a" * 40)
    assert isinstance(result, Failure)
    assert result.what == "env" and "timed out" in result.why


def test_source_inspection_reads_only_the_extracted_revision(tmp_path: Path):
    from lcstatus.evidence import condition_fingerprint
    from lcstatus.sources import Revision
    from lcstatus.verify import Runner

    tree = tmp_path / "exact-tree"
    tree.mkdir()
    (tree / "scheduler.rs").write_text("pump_connect_queue")

    class Mirrors:
        def extract(self, repo, sha, destination):
            assert repo == "ew" and sha == "e" * 40
            return tree

    class GitHub:
        pass

    condition = {
        "id": "condition", "kind": "source_inspection", "check": "inspect.scheduler",
        "proves": "the queue is pumped",
    }
    runner = Runner(Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(), {
        "repos": {"ew": {}},
        "tasks": [{"id": "task", "conditions": [condition]}],
    })
    record = runner.source_inspection(
        "inspect.scheduler",
        {"repo": "ew", "paths": ["scheduler.rs", "missing.rs"],
         "markers": {"queue pump": "pump_connect_queue"}},
        Revision("ew", "e" * 40, "2026-09-11T00:00:00Z", "head"),
        ["condition"],
        ["task"],
    )
    assert record.verdict == "inconclusive"
    assert record.detail["marker_hits"] == {"queue pump": ["scheduler.rs"]}
    assert record.detail["missing_paths"] == ["missing.rs"]
    assert "1/1 configured markers found" in record.summary
    assert record.source["condition_fingerprints"] == {
        "condition": condition_fingerprint(condition),
    }



def test_prepare_owned_symlink_refuses_foreign_symlink(tmp_path: Path):
    from lcstatus.verify import prepare_owned_symlink

    owned = tmp_path / "owned"
    owned.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    link = tmp_path / "watcher-main"
    link.symlink_to(foreign, target_is_directory=True)

    failure = prepare_owned_symlink(link, owned, owned)
    assert failure is not None and failure.what == "compatibility_path"
    assert link.is_symlink() and link.resolve() == foreign.resolve()


def test_cross_app_runner_uses_all_exact_trees_and_isolated_home(tmp_path: Path, monkeypatch):
    import lcstatus.verify as verify
    from lcstatus.sources import Revision

    trees = {}
    for repo in ("invoice-processor", "eom-email-watcher", "connect-contracts"):
        tree = tmp_path / repo
        tree.mkdir()
        trees[repo] = tree
    scripts = trees["invoice-processor"] / "scripts"
    scripts.mkdir()
    (scripts / "accept_against_email_watcher.py").write_text("raise SystemExit(0)\n")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)

    class Mirrors:
        def extract(self, repo, sha, destination):
            return trees[repo]

    class GitHub:
        pass

    runner = verify.Runner(
        Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(),
        {"repos": {repo: {} for repo in trees}},
    )
    monkeypatch.setattr(runner, "_venv", lambda *args, **kwargs: venv)
    monkeypatch.setattr(verify, "WATCHER_COMPAT_PATH", tmp_path / "watcher-main")
    monkeypatch.setattr(verify, "WATCHER_COMPAT_LOCK", tmp_path / "watcher-main.lock")
    captured = {}

    def completed(cmd, **kwargs):
        captured.update({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(cmd, 0, stdout="accepted\n", stderr="")

    monkeypatch.setattr(verify.subprocess, "run", completed)
    revisions = {
        repo: Revision(repo, char * 40, "2026-09-11T00:00:00Z", repo)
        for repo, char in zip(trees, "abc")
    }
    record = runner.accept_ew_ip(
        "xapp.accept", {"participants": list(trees)}, revisions, ["condition"], ["task"]
    )

    assert record.verdict == "pass"
    assert record.participants == {repo: revisions[repo].sha for repo in trees}
    isolated_home = Path(captured["env"]["HOME"])
    assert (isolated_home / "Desktop/invoice-processor").resolve() == trees["invoice-processor"].resolve()
    assert (isolated_home / "Desktop/connect-contracts").resolve() == trees["connect-contracts"].resolve()
    assert captured["cwd"] == trees["invoice-processor"]
    assert record.detail["watcher_tree"] == str(trees["eom-email-watcher"])
    assert record.detail["contracts_tree"] == str(trees["connect-contracts"])
    assert not verify.WATCHER_COMPAT_PATH.exists()


def _pdf_handoff_trees(tmp_path: Path) -> dict[str, Path]:
    trees = {
        repo: tmp_path / repo
        for repo in ("eom-email-watcher", "document-summarizer", "connect-contracts")
    }
    for tree in trees.values():
        tree.mkdir()
    proof = trees["eom-email-watcher"] / "scripts/connect-local-proof.py"
    proof.parent.mkdir()
    proof.write_text("raise SystemExit(0)\n")
    pdf = trees["document-summarizer"] / "src-tauri/tests/fixtures/structured_report.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n")
    provider = trees["document-summarizer"] / "src-tauri/target/release/document-summarizer"
    provider.parent.mkdir(parents=True)
    provider.write_text("binary")
    fixtures = trees["connect-contracts"] / "entitlements/v1/fixtures"
    (fixtures / "valid").mkdir(parents=True)
    for path in (fixtures / "test-keyring.json", fixtures / "valid/active.json", fixtures / "valid/expired.json"):
        path.write_text("{}")
    (trees["connect-contracts"] / "requirements-dev.txt").write_text("jsonschema==4.25.1\n")
    return trees


def test_pdf_handoff_runner_binds_real_proof_to_all_exact_trees(tmp_path: Path, monkeypatch):
    import lcstatus.verify as verify
    from lcstatus.sources import Revision

    trees = _pdf_handoff_trees(tmp_path)

    class Mirrors:
        def extract(self, repo, sha, destination):
            return trees[repo]

    class GitHub:
        pass

    runner = verify.Runner(
        Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(),
        {"repos": {repo: {} for repo in trees}},
    )
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    monkeypatch.setattr(runner, "_venv", lambda *args, **kwargs: venv)
    monkeypatch.setattr(verify, "find_tool", lambda name: f"/tools/{name}")
    monkeypatch.setenv("PYTHONPATH", "/developer/checkout/src")
    monkeypatch.setenv("PYTHONHOME", "/developer/python")
    calls = []

    def completed(cmd, **kwargs):
        calls.append((cmd, kwargs))
        stdout = '{"all_checks":true}\n' if "connect-local-proof.py" in " ".join(cmd) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(verify.subprocess, "run", completed)
    revisions = {
        repo: Revision(repo, char * 40, "2026-09-11T00:00:00Z", repo)
        for repo, char in zip(trees, "abc")
    }
    check = {"participants": list(trees), "repo": "eom-email-watcher", "heavy": True}
    record = runner.accept_ew_ds(
        "xapp.accept_ew_to_ds", check, revisions, ["condition"], ["task"],
    )

    assert record.verdict == "pass" and record.executed == 1 and record.failed == 0
    assert record.participants == {repo: revisions[repo].sha for repo in trees}
    assert calls[0][0][:5] == [
        "/tools/uv", "run", "--quiet", "--with-requirements",
        str(trees["connect-contracts"] / "requirements-dev.txt"),
    ]
    assert calls[0][1]["cwd"] == trees["connect-contracts"]
    assert calls[1][0] == ["/tools/npm", "install", "--silent"]
    assert calls[2][0] == ["/tools/npm", "run", "desktop:build:no-bundle"]
    proof_cmd, proof_kwargs = calls[3]
    assert proof_cmd[:2] == ["/tools/xvfb-run", "-a"]
    assert str(trees["eom-email-watcher"] / "scripts/connect-local-proof.py") in proof_cmd
    assert str(trees["document-summarizer"] / "src-tauri/target/release/document-summarizer") in proof_cmd
    assert str(trees["connect-contracts"] / "entitlements/v1/fixtures/test-keyring.json") in proof_cmd
    assert proof_kwargs["cwd"] == trees["eom-email-watcher"]
    assert proof_kwargs["env"]["LIBGL_ALWAYS_SOFTWARE"] == "1"
    assert proof_kwargs["env"]["WEBKIT_DISABLE_DMABUF_RENDERER"] == "1"
    assert "PYTHONPATH" not in proof_kwargs["env"] and "PYTHONHOME" not in proof_kwargs["env"]
    assert record.detail["document_summarizer_tree"] == str(trees["document-summarizer"])


def test_pdf_handoff_runner_records_nonzero_proof_as_failure(tmp_path: Path, monkeypatch):
    import lcstatus.verify as verify
    from lcstatus.sources import Revision

    trees = _pdf_handoff_trees(tmp_path)

    class Mirrors:
        def extract(self, repo, sha, destination):
            return trees[repo]

    runner = verify.Runner(
        Mirrors(), tmp_path / "cache", tmp_path / "logs", object(),
        {"repos": {repo: {} for repo in trees}},
    )
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    monkeypatch.setattr(runner, "_venv", lambda *args, **kwargs: venv)
    monkeypatch.setattr(verify, "find_tool", lambda name: f"/tools/{name}")
    calls = []

    def completed(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) < 4:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 17, stdout="", stderr="proof broke\n")

    monkeypatch.setattr(verify.subprocess, "run", completed)
    revisions = {
        repo: Revision(repo, char * 40, "2026-09-11T00:00:00Z", repo)
        for repo, char in zip(trees, "abc")
    }
    record = runner.accept_ew_ds(
        "xapp.accept_ew_to_ds", {"participants": list(trees)}, revisions,
        ["condition"], ["task"],
    )

    assert record.verdict == "fail" and record.exit_code == 17
    assert record.summary == "proof broke"
    assert record.executed == 1 and record.failed == 1
    assert record.participants == {repo: revisions[repo].sha for repo in trees}



def test_release_record_uses_target_commit_time_not_publication_time(tmp_path: Path):
    from lcstatus.sources import Revision
    from lcstatus.verify import Runner

    target = "f" * 40
    commit_time = "2026-09-10T09:00:00+00:00"

    class Mirrors:
        def commit_time(self, repo, sha):
            assert repo == "app" and sha == target
            return commit_time

    class GitHub:
        def releases(self, repo):
            return [{
                "tag_name": "v1", "published_at": "2026-09-11T20:00:00Z",
                "draft": False, "prerelease": False, "assets": [],
            }]

        def tag_commit(self, repo, tag):
            return target

    runner = Runner(
        Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(),
        {"repos": {"app": {"github": "example/app"}}},
    )
    record = runner.releases(
        "app.release", {"runner": "github_release", "repo": "*"}, "app",
        Revision("app", target, commit_time, "release"), ["condition"], ["task"]
    )
    assert record.verdict == "pass"
    assert record.revision == target
    assert record.revision_time == commit_time


def test_release_checksum_download_failure_is_unavailable(tmp_path: Path):
    from lcstatus.sources import Failure, Revision
    from lcstatus.verify import Runner

    target = "f" * 40
    assets = [
        {"id": 1, "name": "app.exe", "state": "uploaded", "size": 100},
        {"id": 2, "name": "app.deb", "state": "uploaded", "size": 100},
        {"id": 3, "name": "SHA256SUMS", "state": "uploaded", "size": 100},
    ]

    class Mirrors:
        pass

    class GitHub:
        def releases(self, repo):
            return [{"tag_name": "v1", "draft": False, "prerelease": False, "assets": assets}]

        def release_asset_text(self, repo, asset_id):
            return Failure("gh_release_asset", "download timed out")

    runner = Runner(Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(),
                    {"repos": {"app": {"github": "example/app"}}})
    record = runner.releases(
        "app.release", {
            "runner": "github_release", "repo": "*",
            "required_assets": {
                "windows": r"\.exe$", "linux": r"\.deb$", "checksums": r"SHA256SUMS$",
            },
        }, "app", Revision("app", target, "2026-09-11T00:00:00Z", "head"),
        ["condition"], ["task"],
    )

    assert record.verdict == "unavailable"
    assert record.summary == "release v1 checksum could not be read: download timed out"


def test_release_asset_download_rejects_binary_checksum_content(monkeypatch):
    from lcstatus.sources import Failure, GitHub

    github = GitHub()
    github.available = True
    monkeypatch.setattr(
        "lcstatus.sources.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=b"\xff", stderr=b""),
    )

    result = github.release_asset_text("example/app", 3)

    assert isinstance(result, Failure)
    assert result.what == "gh_release_asset"
    assert result.why == "asset is not UTF-8 checksum text"


def test_default_branch_head_requires_a_full_commit_sha(monkeypatch):
    from lcstatus.sources import Failure, GitHub

    github = GitHub()
    github.available = True

    def result_for(sha):
        monkeypatch.setattr(
            github, "api",
            lambda path: (
                {"default_branch": "main"}
                if path == "repos/example/app"
                else {"commit": {"sha": sha, "commit": {"message": "head"}}}
            ),
        )
        return github.default_branch_head("example/app")

    for malformed in (None, "a" * 39, "g" * 40):
        assert isinstance(result_for(malformed), Failure)
    assert result_for("a" * 40)["sha"] == "a" * 40


def test_web_service_creates_generated_site_before_serving_it():
    unit = (Path(__file__).resolve().parent.parent / "systemd/local-connect-status-web.service").read_text()

    assert "WorkingDirectory=%h/Desktop/local-connect-status\n" in unit
    assert "ExecStartPre=/usr/bin/mkdir -p site\n" in unit
    assert "python3 -m http.server 8790 --directory site --bind 127.0.0.1\n" in unit


def test_editable_install_timeout_becomes_an_explicit_failure(tmp_path: Path, monkeypatch):
    from lcstatus.sources import Failure
    from lcstatus.verify import Runner

    class Mirrors:
        pass

    class GitHub:
        pass

    runner = Runner(Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(), {"repos": {"ip": {}}})
    tree = tmp_path / "tree"
    extra = tmp_path / "extra"
    tree.mkdir()
    extra.mkdir()
    monkeypatch.setattr("lcstatus.verify.shutil.which", lambda name: "/usr/bin/uv")
    calls = 0

    def sync_then_timeout(cmd, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise subprocess.TimeoutExpired(cmd, 900)

    monkeypatch.setattr("lcstatus.verify.subprocess.run", sync_then_timeout)
    result = runner._venv("ip", tree, "a" * 40, extra_trees=[extra])
    assert isinstance(result, Failure)
    assert result.what == "env" and result.why == "editable install timed out after 900 seconds"
    assert result.detail["extra"] == str(extra)

@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (subprocess.TimeoutExpired(["pnpm", "install"], 900), "pnpm install timed out after 900 seconds"),
        (FileNotFoundError(), "pnpm install could not start: FileNotFoundError"),
    ],
)
def test_desktop_dependency_setup_errors_become_explicit_failures(tmp_path: Path, monkeypatch, error, expected):
    from lcstatus.sources import Failure
    from lcstatus.verify import Runner

    class Mirrors:
        pass

    class GitHub:
        pass

    runner = Runner(Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(), {"repos": {}})
    tree = tmp_path / "tree"
    desktop = tree / "desktop"
    desktop.mkdir(parents=True)
    (desktop / "package.json").write_text("{}")
    (desktop / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
    monkeypatch.setattr("lcstatus.verify.find_tool", lambda name: "/usr/bin/pnpm")

    def fail_to_run(*args, **kwargs):
        raise error

    monkeypatch.setattr("lcstatus.verify.subprocess.run", fail_to_run)
    result = runner._desktop_deps(tree)
    assert isinstance(result, Failure)
    assert result.what == "env" and result.why == expected
    assert result.detail == {"directory": str(desktop)}
    assert not (desktop / ".lcstatus-pnpm-ready").exists()


def test_desktop_dependency_cache_requires_success_marker_for_current_lockfile(tmp_path: Path, monkeypatch):
    from lcstatus.verify import Runner

    class Mirrors:
        pass

    class GitHub:
        pass

    runner = Runner(Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(), {"repos": {}})
    tree = tmp_path / "tree"
    desktop = tree / "desktop"
    (desktop / "node_modules").mkdir(parents=True)
    (desktop / "package.json").write_text('{"name":"desktop"}')
    lockfile = desktop / "pnpm-lock.yaml"
    lockfile.write_text("lockfileVersion: 9\n")
    monkeypatch.setattr("lcstatus.verify.find_tool", lambda name: "/usr/bin/pnpm")
    calls = []

    def successful_install(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("lcstatus.verify.subprocess.run", successful_install)
    assert runner._desktop_deps(tree) is None
    assert len(calls) == 1
    assert (desktop / ".lcstatus-pnpm-ready").is_file()
    assert runner._desktop_deps(tree) is None
    assert len(calls) == 1

    (desktop / ".lcstatus-pnpm-ready").write_bytes(b"\xff")
    assert runner._desktop_deps(tree) is None
    assert len(calls) == 2

    lockfile.write_text("lockfileVersion: 9\nchanged: true\n")
    assert runner._desktop_deps(tree) is None
    assert len(calls) == 3


def test_pytest_clears_inherited_python_paths_and_records_startup_error(tmp_path: Path, monkeypatch):
    from lcstatus.sources import Revision
    from lcstatus.verify import Runner

    tree = tmp_path / "tree"
    tree.mkdir()
    venv = tree / ".venv"
    (venv / "bin").mkdir(parents=True)

    class Mirrors:
        def extract(self, repo, sha, destination):
            return tree

    class GitHub:
        pass

    runner = Runner(Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(), {"repos": {"ip": {}}})
    monkeypatch.setattr(runner, "_venv", lambda *args, **kwargs: venv)
    monkeypatch.setenv("PYTHONPATH", "/developer/checkout/src")
    monkeypatch.setenv("PYTHONHOME", "/developer/python")
    captured = {}

    def fail_to_start(*args, **kwargs):
        captured.update(kwargs["env"])
        raise FileNotFoundError()

    monkeypatch.setattr("lcstatus.verify.subprocess.run", fail_to_start)
    record = runner.pytest(
        "ip.test", {"repo": "ip", "args": []},
        Revision("ip", "a" * 40, "2026-09-11T00:00:00+00:00", "head"), ["condition"], ["task"],
    )
    assert record.verdict == "unavailable"
    assert record.summary == "could not start: FileNotFoundError"
    assert "PYTHONPATH" not in captured and "PYTHONHOME" not in captured
    assert captured["PYTHONNOUSERSITE"] == "1"


def test_cargo_startup_error_becomes_unavailable_evidence(tmp_path: Path, monkeypatch):
    from lcstatus.sources import Revision
    from lcstatus.verify import Runner

    tree = tmp_path / "tree"
    (tree / "src-tauri").mkdir(parents=True)

    class Mirrors:
        def extract(self, repo, sha, destination):
            return tree

    class GitHub:
        pass

    runner = Runner(Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(), {"repos": {"ds": {}}})
    monkeypatch.setattr("lcstatus.verify.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("lcstatus.verify.subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    record = runner.cargo_lib(
        "ds.cargo", {"repo": "ds"},
        Revision("ds", "b" * 40, "2026-09-11T00:00:00+00:00", "head"), ["condition"], ["task"],
    )
    assert record.verdict == "unavailable"
    assert record.summary == "could not start npm install --silent: FileNotFoundError"


def test_cross_app_startup_error_becomes_unavailable_evidence(tmp_path: Path, monkeypatch):
    import lcstatus.verify as verify
    from lcstatus.sources import Revision

    trees = {}
    for repo in ("invoice-processor", "eom-email-watcher", "connect-contracts"):
        tree = tmp_path / repo
        tree.mkdir()
        trees[repo] = tree
    scripts = trees["invoice-processor"] / "scripts"
    scripts.mkdir()
    (scripts / "accept_against_email_watcher.py").write_text("raise SystemExit(0)\n")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)

    class Mirrors:
        def extract(self, repo, sha, destination):
            return trees[repo]

    class GitHub:
        pass

    runner = verify.Runner(
        Mirrors(), tmp_path / "cache", tmp_path / "logs", GitHub(),
        {"repos": {repo: {} for repo in trees}},
    )
    monkeypatch.setattr(runner, "_venv", lambda *args, **kwargs: venv)
    monkeypatch.setattr(verify, "WATCHER_COMPAT_PATH", tmp_path / "watcher-main")
    monkeypatch.setattr(verify, "WATCHER_COMPAT_LOCK", tmp_path / "watcher-main.lock")
    monkeypatch.setattr(verify.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    revisions = {
        repo: Revision(repo, char * 40, "2026-09-11T00:00:00+00:00", repo)
        for repo, char in zip(trees, "abc")
    }
    record = runner.accept_ew_ip(
        "xapp.accept", {"participants": list(trees)}, revisions, ["condition"], ["task"]
    )
    assert record.verdict == "unavailable"
    assert record.summary == "could not start: FileNotFoundError"
    assert not verify.WATCHER_COMPAT_PATH.exists()
