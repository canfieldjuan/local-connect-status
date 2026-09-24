"""Contract 06: the nightly heavy timer and its installer."""

from __future__ import annotations

import configparser
from pathlib import Path

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
    assert service["ExecStart"].endswith("-m lcstatus.collect --heavy-only")
    assert service["Nice"] == "19"
    assert service["IOSchedulingClass"] == "idle"
    assert service["CPUWeight"] == "20"
    assert service["TimeoutStartSec"] == "3h"
    assert service["WorkingDirectory"] == unit("local-connect-status.service")["Service"]["WorkingDirectory"]
    timer = unit("local-connect-status-heavy.timer")["Timer"]
    assert timer["OnCalendar"] == "*-*-* 03:30:00"
    assert timer["Persistent"] == "true"
    assert timer["Unit"] == "local-connect-status-heavy.service"
    routine = unit("local-connect-status.service")["Service"]
    assert "--heavy" not in routine["ExecStart"]               # the routine tick never selects heavy checks

    installer = (SYSTEMD / "install.sh").read_text()
    install_part, uninstall_part = installer.split("\nfi\n", 1)[1], installer.split("\nfi\n", 1)[0]
    for name in ("local-connect-status-heavy.service", "local-connect-status-heavy.timer"):
        assert name in install_part, name                      # copied on install
        assert name in uninstall_part, name                    # removed on uninstall
    enable = next(line for line in install_part.splitlines() if "enable --now" in line)
    disable = next(line for line in uninstall_part.splitlines() if "disable --now" in line)
    assert "local-connect-status-heavy.timer" in enable and "local-connect-status-heavy.timer" in disable
