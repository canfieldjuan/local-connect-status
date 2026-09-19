"""One immutable log set per stored evidence record (docs/contracts/02-immutable-per-record-logs.md).

Every execution writes its own files; the files of an execution the store collapses as an identical
consecutive observation are removed; a stored record's files are never overwritten or lost.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lcstatus.collect import ROOT, store_result
from lcstatus.evidence import Record, Store
from lcstatus.sources import Revision


def _runner(tmp_path: Path, monkeypatch):
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
    return runner


def _junit(tests: int, failures: int, skipped: int) -> str:
    return (f'<testsuites><testsuite tests="{tests}" failures="{failures}" errors="0" '
            f'skipped="{skipped}"/></testsuites>')


def _fake_pytest(monkeypatch, outcomes: list[tuple[int, str, str | None]]):
    """A pytest stand-in: each call consumes (returncode, stdout, junit-xml-or-None) and writes the
    JUnit file exactly where the runner asked, as pytest would. Interpreter version calls answer too."""
    queue = list(outcomes)

    def run(cmd, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="Python 3.13.0\n", stderr="")
        rc, stdout, junit = queue.pop(0)
        if isinstance(rc, BaseException):
            raise rc
        target = next(arg for arg in cmd if arg.startswith("--junitxml=")).split("=", 1)[1]
        if junit is not None:
            Path(target).write_text(junit)
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")

    monkeypatch.setattr("lcstatus.verify.subprocess.run", run)


REV = Revision("ip", "a" * 40, "2026-09-11T00:00:00+00:00", "head")


def _pytest(runner) -> Record:
    return runner.pytest("ip.test", {"repo": "ip", "args": []}, REV, ["condition"], ["task"])


def _files(logs: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(logs.iterdir())}


def test_identical_executions_keep_one_log_set(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    _fake_pytest(monkeypatch, [(0, "first run\n3 passed in 0.31s", _junit(3, 0, 0)),
                               (0, "second run\n3 passed in 0.29s", _junit(3, 0, 0))])
    store, failures = Store(tmp_path / "records.jsonl"), []

    first = _pytest(runner)
    store_result(store, failures, first)
    second = _pytest(runner)
    store_result(store, failures, second)

    assert first.verdict == second.verdict == "pass"
    assert len(store) == 1                                  # collapsed as an identical observation
    assert second.log_path != first.log_path
    first_log = Path(first.log_path)
    assert _files(tmp_path / "logs") == {
        first_log.name: first_log.read_text(),
        first_log.with_suffix(".junit.xml").name: _junit(3, 0, 0),
    }
    assert first_log.read_text().startswith("first run")  # the stored record's evidence, untouched
    assert not Path(second.log_path).exists()
    assert failures == []


def test_changed_result_keeps_both_log_sets(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    _fake_pytest(monkeypatch, [(0, "green\n3 passed", _junit(3, 0, 0)),
                               (1, "red\n1 failed, 2 passed", _junit(3, 1, 0))])
    store, failures = Store(tmp_path / "records.jsonl"), []

    first = _pytest(runner)
    store_result(store, failures, first)
    before = _files(tmp_path / "logs")
    second = _pytest(runner)
    store_result(store, failures, second)

    assert (first.verdict, second.verdict) == ("pass", "fail")
    assert len(store) == 2
    after = _files(tmp_path / "logs")
    assert {name: after[name] for name in before} == before   # the first set is untouched, byte for byte
    assert set(after) - set(before) == {Path(second.log_path).name,
                                        Path(second.log_path).with_suffix(".junit.xml").name}
    assert Path(second.log_path).read_text().startswith("red")


def test_collapsed_execution_removal_failure_is_only_a_warning(tmp_path: Path, monkeypatch, capsys):
    runner = _runner(tmp_path, monkeypatch)
    _fake_pytest(monkeypatch, [(0, "one\n1 passed", _junit(1, 0, 0)), (0, "two\n1 passed", _junit(1, 0, 0))])
    store, failures = Store(tmp_path / "records.jsonl"), []
    store_result(store, failures, _pytest(runner))
    second = _pytest(runner)
    logs = tmp_path / "logs"
    logs.chmod(0o500)                                       # unlink now raises PermissionError
    try:
        store_result(store, failures, second)
    finally:
        logs.chmod(0o700)

    assert len(store) == 1 and failures == []
    assert second.verdict == "pass"
    assert Path(second.log_path).exists()                   # left behind, reported, nothing else changes
    assert "warning: could not remove" in capsys.readouterr().err


def test_timeout_and_startup_failure_name_only_what_was_written(tmp_path: Path, monkeypatch):
    import lcstatus.verify as verify

    runner = _runner(tmp_path, monkeypatch)
    _fake_pytest(monkeypatch, [(subprocess.TimeoutExpired(["pytest"], 3600), "", None),
                               (FileNotFoundError(), "", None)])
    timed_out = _pytest(runner)
    could_not_start = _pytest(runner)
    assert (timed_out.verdict, timed_out.log_path) == ("unavailable", None)
    assert (could_not_start.verdict, could_not_start.log_path) == ("unavailable", None)
    assert list((tmp_path / "logs").iterdir()) == []

    # cargo_lib keeps the output of the steps that completed and names that file
    monkeypatch.setattr(verify.shutil, "which", lambda name: f"/tools/{name}")
    calls = []

    def steps(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["npm", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="installed\n", stderr="")
        raise subprocess.TimeoutExpired(cmd, 3600)

    monkeypatch.setattr("lcstatus.verify.subprocess.run", steps)
    record = runner.cargo_lib("ds.lib", {"repo": "ip"}, REV, ["condition"], ["task"])
    assert record.verdict == "unavailable" and record.summary == "timeout in npm run build"
    written = list((tmp_path / "logs").iterdir())
    assert written == [Path(record.log_path)]
    assert "installed" in written[0].read_text()


def test_legacy_fixed_name_files_are_never_touched(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    legacy = tmp_path / "logs" / "ip.test.aaaaaaaaaaaa.log"
    legacy.write_text("output of some earlier execution")
    legacy_junit = tmp_path / "logs" / "ip.test.aaaaaaaaaaaa.junit.xml"
    legacy_junit.write_text(_junit(9, 9, 9))
    _fake_pytest(monkeypatch, [(0, "fresh\n1 passed", _junit(1, 0, 0))])

    record = _pytest(runner)

    assert Path(record.log_path) not in (legacy, legacy_junit)
    assert legacy.read_text() == "output of some earlier execution"
    assert legacy_junit.read_text() == _junit(9, 9, 9)
    assert record.executed == 1                             # counted from its own JUnit, not the legacy one


def test_execution_names_carry_the_start_instant_and_never_collide(tmp_path: Path, monkeypatch):
    import lcstatus.verify as verify

    runner = _runner(tmp_path, monkeypatch)
    fixed = datetime(2026, 9, 19, 15, 44, 51, 123456, tzinfo=timezone.utc)

    class Clock:
        @staticmethod
        def now(tz=None):
            return fixed

    monkeypatch.setattr(verify, "datetime", Clock)
    log, junit = runner._execution_paths("ip.test", "aaaaaaaaaaaa")
    assert log.name == "ip.test.aaaaaaaaaaaa.20260919T154451123456Z.log"
    assert junit.name == "ip.test.aaaaaaaaaaaa.20260919T154451123456Z.junit.xml"

    junit.write_text("taken")                               # either file at the name reserves the stem
    again_log, again_junit = runner._execution_paths("ip.test", "aaaaaaaaaaaa")
    assert again_log.name == "ip.test.aaaaaaaaaaaa.20260919T154451123456Z.1.log"
    assert again_junit.name == "ip.test.aaaaaaaaaaaa.20260919T154451123456Z.1.junit.xml"
    assert junit.read_text() == "taken"


def test_recorded_command_omits_the_per_execution_junit_path(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    _fake_pytest(monkeypatch, [(0, "a\n1 passed", _junit(1, 0, 0)), (0, "b\n1 passed", _junit(1, 0, 0))])

    first, second = _pytest(runner), _pytest(runner)

    assert first.command == second.command                  # identity survives the per-execution name
    assert "--junitxml" not in first.command
    assert first.command.startswith("[Python 3.13.0] ")
    assert first.command.endswith("/.venv/bin/python -m pytest -p no:cacheprovider")
    assert first.executed == second.executed == 1           # counts still come from each own JUnit


def test_execution_files_follow_the_record_not_the_runner():
    from lcstatus.verify import execution_files

    assert execution_files(Record(kind="ci_run", repo="ip", revision="a" * 40, verdict="pass")) == []
    pytest_record = Record(kind="automated_test", repo="ip", revision="a" * 40, verdict="pass",
                           log_path="/logs/ip.test.aaaaaaaaaaaa.20260919T154451123456Z.log")
    assert execution_files(pytest_record) == [
        Path("/logs/ip.test.aaaaaaaaaaaa.20260919T154451123456Z.log"),
        Path("/logs/ip.test.aaaaaaaaaaaa.20260919T154451123456Z.junit.xml"),
    ]


def test_render_only_writes_and_removes_nothing(tmp_path: Path):
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "ip.test.aaaaaaaaaaaa.log").write_text("kept")
    (data / "records.jsonl").write_text(Record(
        kind="automated_test", repo="invoice-processor", revision="a" * 40, verdict="pass",
        log_path=str(data / "logs" / "ip.test.aaaaaaaaaaaa.log"), condition_ids=["ip.suite_green"],
        source={"type": "local_runner", "check": "ip.pytest.all"},
    ).to_json() + "\n")
    (data / "state.json").write_text(json.dumps({"heads": {}, "change_baselines": {}, "runs": 1,
                                                 "last_run_at": "2026-09-19T00:00:00+00:00",
                                                 "last_failures": []}))

    result = subprocess.run([sys.executable, "-m", "lcstatus.collect", "--data", str(data),
                             "--site", str(tmp_path / "site"), "--render-only"],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr
    assert _files(data / "logs") == {"ip.test.aaaaaaaaaaaa.log": "kept"}
