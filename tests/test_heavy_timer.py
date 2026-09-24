"""Contract 06: the nightly heavy timer and its installer."""

from __future__ import annotations

import configparser
import os
import subprocess
import time
from pathlib import Path

import pytest

from lcstatus.verify import run_owned_group

ROOT = Path(__file__).resolve().parent.parent
SYSTEMD = ROOT / "systemd"


def unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str                                   # systemd keys are case-sensitive
    parser.read_string((SYSTEMD / name).read_text())
    return parser


def test_heavy_units_are_installed_and_bounded():
    service = unit("local-connect-status-heavy.service")["Service"]
    assert service["Type"] == "oneshot"
    routine = unit("local-connect-status.service")["Service"]
    assert "--heavy" not in routine["ExecStart"]               # the routine tick never selects heavy checks
    assert service["ExecStart"] == routine["ExecStart"] + " --heavy-only"
    assert service["Environment"] == routine["Environment"]
    assert service["WorkingDirectory"] == routine["WorkingDirectory"]
    assert service["Nice"] == "19"
    assert service["IOSchedulingClass"] == "idle"
    assert service["CPUWeight"] == "20"
    assert service["TimeoutStartSec"] == routine["TimeoutStartSec"] == "infinity"   # runners bound their steps
    heavy_timer = unit("local-connect-status-heavy.timer")
    timer = heavy_timer["Timer"]
    assert timer["OnCalendar"] == "*-*-* 03,04,05:30:00"      # the attempt and two retries
    assert timer["RandomizedDelaySec"] == "10min"
    assert timer["Persistent"] == "true"
    assert timer["Unit"] == "local-connect-status-heavy.service"
    assert heavy_timer["Install"]["WantedBy"] == "timers.target"

    installer = (SYSTEMD / "install.sh").read_text()
    install_part, uninstall_part = installer.split("\nfi\n", 1)[1], installer.split("\nfi\n", 1)[0]
    for name in ("local-connect-status-heavy.service", "local-connect-status-heavy.timer"):
        assert name in install_part, name                      # copied on install
        assert name in uninstall_part, name                    # removed on uninstall
    enable = next(line for line in install_part.splitlines() if "enable --now" in line)
    disable = next(line for line in uninstall_part.splitlines() if "disable --now" in line)
    assert "local-connect-status-heavy.timer" in enable and "local-connect-status-heavy.timer" in disable


def test_the_proof_owns_its_process_group(tmp_path: Path):
    """Real processes: a wrapper that leaves a long-lived background child and outlives its timeout.

    The child must outlive every wait inside run_owned_group (60 s to drain after the kill); otherwise a
    child-only kill would look correct because the child had simply finished."""
    import signal
    marker = tmp_path / "child.pid"
    started = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        run_owned_group(["sh", "-c", f"sleep 600 & echo $! > {marker}; wait"], cwd=tmp_path,
                        env=dict(os.environ), timeout=1)
    elapsed = time.time() - started
    child = int(marker.read_text())
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            stat = Path(f"/proc/{child}/stat")
            if not stat.exists() or stat.read_text().split()[2] == "Z":
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"background child {child} outlived the timeout")
        assert elapsed < 20, f"the timeout took {elapsed:.1f}s: the group was not killed, the pipe stayed open"
    finally:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass
    done = run_owned_group(["sh", "-c", "echo hi; echo err >&2; exit 3"], cwd=tmp_path,
                           env=dict(os.environ), timeout=10)
    assert (done.returncode, done.stdout, done.stderr) == (3, "hi\n", "err\n")
