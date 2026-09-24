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


def _gone(pid: int) -> bool:
    """A process that no longer exists, or only as a zombie.  /proc can vanish between the two reads."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
    except OSError:
        return True


def _wait_gone(pid: int, seconds: float = 5) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _gone(pid):
            return True
        time.sleep(0.05)
    return False


def _kill_if_alive(pid: int) -> None:
    import signal
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_the_proof_owns_its_process_group(tmp_path: Path):
    """Real processes: a wrapper that leaves a long-lived background child and outlives its timeout.

    The child must outlive every wait inside run_owned_group (60 s to drain after the kill); otherwise a
    child-only kill would look correct because the child had simply finished."""
    marker = tmp_path / "child.pid"
    started = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        run_owned_group(["sh", "-c", f"sleep 600 & echo $! > {marker}; wait"], cwd=tmp_path,
                        env=dict(os.environ), timeout=1)
    elapsed = time.time() - started
    child = int(marker.read_text())
    gone = _wait_gone(child)
    if not gone:
        _kill_if_alive(child)                                     # never leave it behind; never kill a reused pid
    assert gone, f"background child {child} outlived the timeout"
    assert elapsed < 20, f"the timeout took {elapsed:.1f}s: the group was not killed, the pipe stayed open"
    done = run_owned_group(["sh", "-c", "echo hi; echo err >&2; exit 3"], cwd=tmp_path,
                           env=dict(os.environ), timeout=10)
    assert (done.returncode, done.stdout, done.stderr) == (3, "hi\n", "err\n")


def test_owned_group_is_gone_when_the_timeout_returns(tmp_path: Path):
    """Members writing to /dev/null (as Xvfb and the provider do) hold no pipe the drain waits on; the
    group must still be gone when the timeout is raised.  Whether killed members are already reaped is
    a race, so the scenario uses ten such members and three rounds: a helper that does not wait for the
    group is caught with near certainty, and one that does passes every time."""
    for round_ in range(3):
        marker = tmp_path / f"group-{round_}.pid"
        members = " ".join(["sleep 600 >/dev/null 2>&1 &"] * 10)
        with pytest.raises(subprocess.TimeoutExpired):
            run_owned_group(["sh", "-c", f"echo $$ > {marker}; {members} sleep 600"],
                            cwd=tmp_path, env=dict(os.environ), timeout=1)
        group = int(marker.read_text())
        with pytest.raises(ProcessLookupError):
            os.killpg(group, 0)


def test_owned_group_decodes_any_bytes(tmp_path: Path):
    done = run_owned_group(["sh", "-c", "printf '\\377\\376ok'"], cwd=tmp_path, env=dict(os.environ), timeout=10)
    assert done.returncode == 0 and done.stdout.endswith("ok") and "\ufffd" in done.stdout
    with pytest.raises(subprocess.TimeoutExpired):                # not UnicodeDecodeError
        run_owned_group(["sh", "-c", "printf '\\377'; sleep 600"], cwd=tmp_path, env=dict(os.environ), timeout=1)


def test_owned_group_ends_the_group_on_interrupt(tmp_path: Path):
    """Ctrl-C on a manual run: SIGINT to the collector must end its step's whole group."""
    import signal
    import sys
    marker = tmp_path / "child.pid"
    script = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from lcstatus.verify import run_owned_group\n"
        f"run_owned_group(['sh', '-c', 'sleep 600 & echo $! > {marker}; wait'], cwd={str(tmp_path)!r},\n"
        "                env=dict(os.environ), timeout=600)\n"
    )
    collector = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 20
        while not (marker.exists() and marker.read_text().strip()) and time.time() < deadline:
            time.sleep(0.05)
        child = int(marker.read_text())
        os.kill(collector.pid, signal.SIGINT)
        collector.wait(timeout=90)
    finally:
        if collector.poll() is None:
            collector.kill()
            collector.wait()
    gone = _wait_gone(child)
    if not gone:
        _kill_if_alive(child)
    assert collector.returncode != 0                              # it was interrupted
    assert gone, f"the step's child {child} outlived the interrupt"


def test_owned_group_gives_a_step_no_input(tmp_path: Path):
    """A step that asks for input sees end of input at once instead of waiting (or being stopped) on a
    terminal it is no longer in the foreground of."""
    started = time.time()
    done = run_owned_group(["sh", "-c", "if read answer; then echo read:$answer; else echo eof; fi"],
                           cwd=tmp_path, env=dict(os.environ), timeout=30)
    assert done.stdout == "eof\n" and time.time() - started < 10

