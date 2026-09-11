"""Load and validate the capability catalogue. A bad catalogue fails loudly, up front."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence import KINDS, PLATFORMS

RUNNERS = ("pytest", "cargo_lib", "accept_ew_ip", "ci_job", "manual_observation", "github_release")
LAYERS = ("standalone", "connect", "automate", "release")


def load(path: Path) -> dict[str, Any]:
    cat = json.loads(Path(path).read_text(encoding="utf-8"))
    problems: list[str] = []
    checks = cat.get("checks", {})
    for cid, chk in checks.items():
        if chk.get("runner") not in RUNNERS:
            problems.append(f"check {cid}: unknown runner {chk.get('runner')!r}")
        if chk.get("repo") not in cat.get("repos", {}) and chk.get("repo") != "*":
            problems.append(f"check {cid}: unknown repo {chk.get('repo')!r}")
        if chk.get("platform", "n/a") not in PLATFORMS:
            problems.append(f"check {cid}: unknown platform")
    seen_tasks: set[str] = set()
    seen_conds: set[str] = set()
    for t in cat.get("tasks", []):
        if t["id"] in seen_tasks:
            problems.append(f"duplicate task id {t['id']}")
        seen_tasks.add(t["id"])
        if t.get("layer") not in LAYERS:
            problems.append(f"task {t['id']}: unknown layer {t.get('layer')!r}")
        app = t.get("app")
        if app != "bundle" and app not in cat.get("apps", {}):
            problems.append(f"task {t['id']}: unknown app {app!r}")
        t["app_repo"] = cat["apps"].get(app, {}).get("repo", "") if app != "bundle" else ""
        for c in t.get("conditions", []):
            if c["id"] in seen_conds:
                problems.append(f"duplicate condition id {c['id']}")
            seen_conds.add(c["id"])
            if c.get("kind") not in KINDS:
                problems.append(f"condition {c['id']}: unknown kind {c.get('kind')!r}")
            if c.get("check") not in checks:
                problems.append(f"condition {c['id']}: unknown check {c.get('check')!r}")
    if problems:
        raise ValueError("catalogue invalid:\n  " + "\n  ".join(problems))
    return cat
