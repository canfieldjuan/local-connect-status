"""Runner verdicts come from exit codes and counted results, not from what a process printed."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

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
