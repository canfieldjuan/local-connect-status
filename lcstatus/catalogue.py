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
    "github_issues",
    "github_release",
    "source_inspection",
)
LAYERS = ("standalone", "connect", "automate", "release")
KIND_RUNNERS = {
    "automated_test": {"pytest", "cargo_lib", "accept_ew_ip", "accept_ew_ds"},
    "ci_run": {"ci_job"},
    "installed_demo": {"manual_observation"},
    "issue_gate": {"github_issues"},
    "release_artifact": {"github_release"},
    "source_inspection": {"source_inspection"},
}


def declared_participants(check: dict[str, Any]) -> list[str]:
    """The repositories a record for this check must name.  Defined here, read everywhere.

    A check that declares `participants` names them all; a check that does not declares its own
    repository.  The observation script writes this set, the rules admit only this set, and the
    validator below checks its shape.  No other module may re-derive it.
    """
    return list(check["participants"]) if "participants" in check else [check["repo"]]


def participants_required(check: dict[str, Any]) -> bool:
    """True for a cross-app check: every record must name the declared set (automated rows may
    name nothing only for a check that declares none)."""
    return "participants" in check


def depends_on_problems(depends_on: Any, cat: dict[str, Any]) -> list[str]:
    """Shape of a task's `depends_on` (contract 08 B2).  Whether each pattern still names a file is
    a fact about the product repositories, checked every tick against their heads, not here."""
    if not isinstance(depends_on, list):
        return ["depends_on must be a list"]
    problems: list[str] = []
    seen: set[str] = set()
    for item in depends_on:
        if not isinstance(item, dict):
            problems.append("each depends_on item must be an object")
            continue
        repo = item.get("repo")
        if not isinstance(repo, str) or repo not in cat.get("repos", {}):
            problems.append(f"depends_on repo must name one catalogue repository, got {repo!r}")
        elif repo in seen:
            problems.append(f"depends_on names {repo} twice; list its paths in one item")
        else:
            seen.add(repo)
        paths = item.get("paths")
        if (
            not isinstance(paths, list) or not paths
            or any(not isinstance(p, str) or not p.strip() for p in paths)
            or len(set(paths)) != len(paths)
        ):
            problems.append(f"depends_on paths for {repo!r} must be a non-empty list of distinct non-empty strings")
    return problems


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
        if chk.get("repo") not in cat.get("repos", {}):
            problems.append(f"check {cid}: repo must name one catalogue repository, got {chk.get('repo')!r}")
        if chk.get("platform", "n/a") not in PLATFORMS:
            problems.append(f"check {cid}: unknown platform")
        if chk.get("runner") in ("accept_ew_ip", "accept_ew_ds") and not participants_required(chk):
            problems.append(f"check {cid}: a cross-app runner must declare participants")
        if participants_required(chk):
            parts = chk["participants"]
            if (
                not isinstance(parts, list) or not parts or len(set(parts)) != len(parts)
                or any(part not in cat.get("repos", {}) for part in parts) or chk.get("repo") not in parts
            ):
                problems.append(
                    f"check {cid}: participants must be a non-empty list of distinct catalogue "
                    "repositories that includes the check's own repository"
                )
        if chk.get("runner") == "github_issues":
            if not isinstance(chk.get("milestone"), str) or not chk["milestone"].strip():
                problems.append(f"check {cid}: GitHub issue gate needs a milestone")
            elif isinstance(issue_gate, dict) and chk["milestone"] != issue_gate.get("milestone"):
                problems.append(f"check {cid}: GitHub issue milestone must match the release issue gate")
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
        if issue_gate is not None and t.get("layer") == "release":
            if not any(c.get("kind") == "issue_gate" for c in t.get("conditions", [])):
                problems.append(f"task {t['id']}: release task needs an issue_gate condition")
        if t.get("layer") != "release" and any(c.get("kind") == "issue_gate" for c in t.get("conditions", [])):
            problems.append(f"task {t['id']}: only release tasks may use issue_gate conditions")
        problems.extend(f"task {t['id']}: {why}" for why in depends_on_problems(t.get("depends_on", []), cat))
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
