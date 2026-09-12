"""Load and validate the capability catalogue. A bad catalogue fails loudly, up front."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence import KINDS, PLATFORMS

RUNNERS = (
    "pytest",
    "cargo_lib",
    "accept_ew_ip",
    "accept_ew_ds",
    "ci_job",
    "manual_observation",
    "github_release",
    "source_inspection",
)
LAYERS = ("standalone", "connect", "automate", "release")
KIND_RUNNERS = {
    "automated_test": {"pytest", "cargo_lib", "accept_ew_ip", "accept_ew_ds"},
    "ci_run": {"ci_job"},
    "installed_demo": {"manual_observation"},
    "release_artifact": {"github_release"},
    "source_inspection": {"source_inspection"},
}


def load(path: Path) -> dict[str, Any]:
    cat = json.loads(Path(path).read_text(encoding="utf-8"))
    problems: list[str] = []
    release = cat.get("release", {})
    issue_gate = release.get("issue_gate")
    if issue_gate is not None:
        if not isinstance(issue_gate, dict):
            problems.append("release issue gate must be an object")
        elif not isinstance(issue_gate.get("milestone"), str) or not issue_gate["milestone"].strip():
            problems.append("release issue gate needs a non-empty milestone")
    checks = cat.get("checks", {})
    for cid, chk in checks.items():
        if chk.get("runner") not in RUNNERS:
            problems.append(f"check {cid}: unknown runner {chk.get('runner')!r}")
        if chk.get("repo") not in cat.get("repos", {}) and chk.get("repo") != "*":
            problems.append(f"check {cid}: unknown repo {chk.get('repo')!r}")
        if chk.get("platform", "n/a") not in PLATFORMS:
            problems.append(f"check {cid}: unknown platform")
        if chk.get("runner") == "source_inspection":
            if not chk.get("paths"):
                problems.append(f"check {cid}: source inspection needs paths")
            if not chk.get("markers"):
                problems.append(f"check {cid}: source inspection needs markers")
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
        issue_repos = t.get("release_issue_repos", [])
        if issue_gate is not None and t.get("layer") == "release" and not issue_repos:
            problems.append(f"task {t['id']}: release task needs release_issue_repos")
        if issue_repos and t.get("layer") != "release":
            problems.append(f"task {t['id']}: only release tasks may define release_issue_repos")
        if not isinstance(issue_repos, list) or any(repo not in cat.get("repos", {}) for repo in issue_repos):
            problems.append(f"task {t['id']}: release_issue_repos must name known repositories")
        for c in t.get("conditions", []):
            if c["id"] in seen_conds:
                problems.append(f"duplicate condition id {c['id']}")
            seen_conds.add(c["id"])
            if c.get("kind") not in KINDS:
                problems.append(f"condition {c['id']}: unknown kind {c.get('kind')!r}")
            if c.get("check") not in checks:
                problems.append(f"condition {c['id']}: unknown check {c.get('check')!r}")
            else:
                runner = checks[c["check"]].get("runner")
                if runner not in KIND_RUNNERS.get(c.get("kind"), set()):
                    problems.append(
                        f"condition {c['id']}: {c.get('kind')!r} cannot use {runner!r} runner"
                    )
    if problems:
        raise ValueError("catalogue invalid:\n  " + "\n  ".join(problems))
    return cat
