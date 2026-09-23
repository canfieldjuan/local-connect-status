"""Runners. Each produces Records with exact revisions, exit codes, and real counts.

Principles that keep these honest:

* Tests run against a tree extracted at the exact revision, never a developer checkout.
* pytest results come from JUnit XML, so "executed / failed / skipped" are counted, not
  parsed from a summary line. A nonzero exit is a failure even if a passing-looking line
  was printed. Zero executed tests is `skip`, never `pass`.
* A GitHub job is recorded as `ci_run` with the job's own conclusion and the platform read
  from the job, not assumed. Pending and cancelled stay pending and skip.
* A runner that cannot run records `unavailable` with the reason. It never returns nothing.
* The cross-app acceptance stages the operator's installed Connect licence into its isolated
  installation, because the products accept only their production authority. A licence that is
  absent, malformed or outside its validity window is `unavailable`, never a product failure.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .catalogue import declared_participants
from .evidence import Record, atomic_write, check_fingerprint, condition_fingerprint_map, now_iso
from .sources import Failure, GitHub, Mirrors, Revision


WATCHER_COMPAT_PATH = Path("/tmp/watcher-main")
WATCHER_COMPAT_LOCK = Path("/tmp/local-connect-status-watcher-main.lock")

# Where the products look for an installed Connect licence on Linux, relative to the configuration
# root, the size they refuse to read past, and the only timestamp grammar they accept. All three
# mirror eom_email_watcher/entitlement.py; drift there surfaces here as an unavailable result.
INSTALLED_ENTITLEMENT = Path("local-connect") / "entitlement-v1.json"
MAX_ENTITLEMENT_BYTES = 16 * 1024
UTC_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
# The acceptance script prints the watcher's own entitlement verdict on this line.
WATCHER_DECISION_PREFIX = "watcher entitlement decision"
INVOICE_ACCEPTANCE_CHECKS = frozenset({
    "consumer_import_pinned", "entitlement_active", "provider_discovered",
    "invoice_job_completed", "declared_outputs_present", "ledger_one_entry",
    "replay_no_duplicate", "second_provider_refused", "registration_removed",
})


def invoice_acceptance_proof(stdout: str) -> dict[str, Any] | None:
    """Accept only the complete, machine-readable final proof line."""
    try:
        proof = json.loads((stdout.strip().splitlines() or [""])[-1])
    except (ValueError, TypeError):
        return None
    if (not isinstance(proof, dict) or type(proof.get("schema_version")) is not int
            or proof["schema_version"] != 1):
        return None
    checks = proof.get("checks")
    if (proof.get("all_checks") is not True or not isinstance(checks, dict)
            or set(checks) != INVOICE_ACCEPTANCE_CHECKS
            or any(value is not True for value in checks.values())):
        return None
    return proof


def _pyproject_has_dev_group(tree: Path) -> bool:
    pp = tree / "pyproject.toml"
    return pp.exists() and "[dependency-groups]" in pp.read_text(encoding="utf-8")


def find_tool(name: str, home: Path | None = None) -> str | None:
    """Locate a developer tool the way a login shell would.

    The systemd user service has a minimal PATH; pnpm lives under nvm or the
    pnpm home. Probe those after PATH so the installed collector and a shell
    run resolve the same binary. Returns the absolute path or None.
    """
    found = shutil.which(name)
    if found:
        return found
    home = home or Path.home()
    candidates = sorted(home.glob(f".nvm/versions/node/*/bin/{name}"), reverse=True)
    candidates += [home / ".local/share/pnpm" / name, home / ".local/bin" / name]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def uv_sync_command(python: str | None) -> list[str]:
    """`uv sync` for a tree, pinned to the catalogue's interpreter when one is declared.

    Without the pin uv picks the newest CPython it can find, and a verdict then
    depends on which interpreter happened to be installed rather than on the
    revision under test (Invoice Processor at 411844569a00 read 1 failed under
    3.14.3 and green under 3.13.11 for the same code).
    """
    cmd = ["uv", "sync", "--quiet", "--all-groups"]
    if python:
        cmd += ["--python", python]
    return cmd


def sanitized_python_env(**updates: str) -> dict[str, str]:
    """Environment for exact-tree Python processes, free of caller import-path overrides."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    env["PYTHONNOUSERSITE"] = "1"
    env.update(updates)
    return env


def interpreter_version(venv: Path) -> str:
    try:
        r = subprocess.run(
            [str(venv / "bin" / "python"), "--version"],
            capture_output=True, text=True, timeout=30, env=sanitized_python_env(),
        )
        return (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return "python (version unknown)"


def _release_assets(
    releases: list[dict], required_assets: dict[str, str]
) -> tuple[dict | None, dict[str, list[dict]], dict[str, Any]]:
    published = [r for r in releases if not r.get("draft") and not r.get("prerelease")]
    if not published:
        return None, {}, {"count": len(releases)}
    latest = published[0]
    assets = latest.get("assets", [])
    matches: dict[str, list[dict]] = {}
    missing: list[str] = []
    for label, pattern in required_assets.items():
        usable = []
        for asset in assets:
            name = asset.get("name") or ""
            try:
                size = int(asset.get("size", 0))
            except (TypeError, ValueError):
                size = 0
            if re.search(pattern, name) and asset.get("state") == "uploaded" and size > 0:
                usable.append(asset)
        matches[label] = usable
        if not usable:
            missing.append(label)
    detail = {
        "tag": latest.get("tag_name"),
        "published_at": latest.get("published_at"),
        "assets": [a.get("name") or "" for a in assets],
        "asset_checks": [
            {"id": a.get("id"), "name": a.get("name") or "", "state": a.get("state"),
             "size": a.get("size"), "digest": a.get("digest")}
            for a in assets
        ],
        "missing": missing,
    }
    return latest, matches, detail


def _asset_key(asset: dict) -> str:
    return str(asset.get("id") or asset.get("name") or "")


def _checksum_entries(content: str) -> dict[str, set[str]]:
    entries: dict[str, set[str]] = {}
    for line in content.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+?)\s*", line)
        if match:
            name = match.group(2).replace("\\", "/").rsplit("/", 1)[-1]
            entries.setdefault(name, set()).add(match.group(1).lower())
    return entries


def _asset_sha256(asset: dict) -> str | None:
    digest = asset.get("digest")
    if not isinstance(digest, str):
        return None
    match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest)
    return match.group(1).lower() if match else None


def release_verdict(
    releases: list[dict], required_assets: dict[str, str],
    checksum_contents: dict[str, str] | None = None,
) -> tuple[str, str, dict, str | None]:
    """Judge a repository's GitHub releases against the catalogue's required assets.

    Returns (verdict, summary, detail, tag). A release counts only if it is published
    (not draft, not prerelease), every required pattern matches an uploaded nonempty
    asset, and each checksum manifest hash matches the corresponding GitHub asset digest.
    """
    latest, matches, detail = _release_assets(releases, required_assets)
    if latest is None:
        return "fail", "no published release", detail, None
    if detail["missing"]:
        return ("fail", f"{latest.get('tag_name')} published but missing or unusable: {', '.join(detail['missing'])}", detail,
                latest.get("tag_name"))
    checksum_labels = [label for label in required_assets if "checksum" in label.lower()]
    if checksum_labels:
        covered: dict[str, set[str]] = {}
        for label in checksum_labels:
            for asset in matches[label]:
                for name, digests in _checksum_entries(
                    (checksum_contents or {}).get(_asset_key(asset), "")
                ).items():
                    covered.setdefault(name, set()).update(digests)
        installers = [
            asset
            for label, assets in matches.items() if label not in checksum_labels
            for asset in assets
        ]
        verification = []
        unverified = []
        for asset in installers:
            name = asset.get("name") or ""
            asset_digest = _asset_sha256(asset)
            manifest_digests = sorted(covered.get(name, set()))
            verified = asset_digest is not None and manifest_digests == [asset_digest]
            verification.append({
                "name": name,
                "asset_digest": asset.get("digest"),
                "manifest_digests": manifest_digests,
                "verified": verified,
            })
            if not verified:
                unverified.append(name)
        detail["checksum_verification"] = verification
        if unverified:
            detail["missing"] = [f"verified checksum for {name}" for name in sorted(unverified)]
            detail["unverified_assets"] = sorted(unverified)
            return ("fail", f"{latest.get('tag_name')} published but checksums do not verify: {', '.join(sorted(unverified))}",
                    detail, latest.get("tag_name"))
    return "pass", f"{latest.get('tag_name')} {latest.get('published_at')}", detail, latest.get("tag_name")


def latest_workflow_runs(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest distinct run wins; attempt only breaks ties within that run."""
    def number(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    def order(run: dict[str, Any]) -> tuple[int, str, int, int]:
        return (
            number(run.get("run_number")),
            str(run.get("created_at") or ""),
            number(run.get("id")),
            number(run.get("run_attempt")),
        )

    selected: dict[str, dict[str, Any]] = {}
    for run in runs:
        name = run.get("name")
        if not isinstance(name, str):
            continue
        if name not in selected or order(run) > order(selected[name]):
            selected[name] = run
    return selected


def prepare_owned_symlink(link: Path, target: Path, owned_root: Path) -> Failure | None:
    """Create a compatibility symlink without removing a path the collector does not own."""
    if link.is_symlink():
        existing = link.resolve(strict=False)
        if not existing.is_relative_to(owned_root.resolve()):
            return Failure("compatibility_path", f"refusing to replace unowned symlink {link}")
        link.unlink()
    elif link.exists():
        return Failure("compatibility_path", f"refusing to remove existing path {link}")
    link.symlink_to(target, target_is_directory=True)
    return None


def installed_entitlement_path(env: Mapping[str, str]) -> Path | Failure:
    """Where this environment's installed Connect licence lives, resolved as the products do on Linux."""
    xdg, home = env.get("XDG_CONFIG_HOME"), env.get("HOME")
    if xdg:
        root = Path(xdg)
    elif home:
        root = Path(home) / ".config"
    else:
        return Failure("entitlement", "neither XDG_CONFIG_HOME nor HOME is set")
    if not root.is_absolute():
        return Failure("entitlement", f"configuration root is not absolute: {root}")
    return root / INSTALLED_ENTITLEMENT


def _instant(value: Any, field_name: str) -> datetime | Failure:
    """A claim timestamp exactly as the watcher parses it: the ``...Z`` grammar, then a UTC instant."""
    if not isinstance(value, str) or not value:
        return Failure("entitlement", f"{field_name} missing")
    if not UTC_TIMESTAMP_PATTERN.match(value):
        return Failure("entitlement", f"{field_name} is not a UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)")
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return Failure("entitlement", f"{field_name} is not a UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)")


def read_installed_entitlement(
    source: Path, now: datetime | None = None,
) -> tuple[bytes, dict[str, str]] | Failure:
    """Read an installed licence and check its shape and validity window, never its signature.

    A Failure here means the environment cannot run the acceptance, so the caller records
    ``unavailable``. Signature validity stays the products' decision; this can never produce a pass.
    """
    now = now or datetime.now(timezone.utc)

    def unreadable(why: str) -> Failure:
        return Failure("entitlement", f"installed Connect entitlement unreadable: {why}")

    try:
        metadata = source.stat()
    except FileNotFoundError:
        return Failure("entitlement", f"no installed Connect entitlement at {source}")
    except OSError as exc:
        return unreadable(type(exc).__name__)
    if not stat.S_ISREG(metadata.st_mode):
        return unreadable("not a regular file")
    if not 0 < metadata.st_size <= MAX_ENTITLEMENT_BYTES:
        return unreadable(f"{metadata.st_size} bytes (limit {MAX_ENTITLEMENT_BYTES})")
    try:
        content = source.read_bytes()
        envelope = json.loads(content)
    except OSError as exc:
        return unreadable(type(exc).__name__)
    except (ValueError, RecursionError):   # deep nesting exhausts the parser, not the collector
        return unreadable("not JSON")
    if not isinstance(envelope, dict):
        return unreadable("not a JSON object")
    if envelope.get("format_version") != 1:
        return unreadable("format_version is not 1")
    key_id = envelope.get("key_id")
    if not isinstance(key_id, str) or not key_id:
        return unreadable("key_id missing")
    payload = envelope.get("payload_base64url")
    if not isinstance(payload, str) or not payload:
        return unreadable("payload_base64url missing")
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, RecursionError):
        return unreadable("payload is not base64url JSON")
    if not isinstance(claims, dict):
        return unreadable("payload is not a JSON object")
    not_before = _instant(claims.get("not_before"), "not_before")
    if isinstance(not_before, Failure):
        return unreadable(not_before.why)
    expires_at = _instant(claims.get("expires_at"), "expires_at")
    if isinstance(expires_at, Failure):
        return unreadable(expires_at.why)
    if now < not_before:
        return Failure("entitlement", f"installed Connect entitlement not valid before {claims['not_before']}")
    if now >= expires_at:
        return Failure("entitlement", f"installed Connect entitlement expired at {claims['expires_at']}")
    return content, {
        "entitlement_source": str(source),
        "entitlement_key_id": key_id,
        "entitlement_not_before": claims["not_before"],
        "entitlement_expires_at": claims["expires_at"],
    }


def stage_entitlement(compat_home: Path, content: bytes) -> Path:
    """Place the licence where the products look for it inside the isolated installation, privately."""
    config = compat_home / ".config"
    for directory in (
        config, config / "local-connect",
        compat_home / ".local", compat_home / ".local" / "share", compat_home / ".local" / "state",
        compat_home / ".cache",
    ):
        directory.mkdir(mode=0o700, exist_ok=True)
    target = config / INSTALLED_ENTITLEMENT
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
    return target


def isolated_xdg_env(compat_home: Path) -> dict[str, str]:
    """XDG base directories inside the isolated installation, so no inherited value escapes it."""
    return {
        "XDG_CONFIG_HOME": str(compat_home / ".config"),
        "XDG_DATA_HOME": str(compat_home / ".local" / "share"),
        "XDG_CACHE_HOME": str(compat_home / ".cache"),
        "XDG_STATE_HOME": str(compat_home / ".local" / "state"),
    }


def watcher_decision(stdout: str) -> str | None:
    """The watcher's own entitlement verdict as the script printed it; diagnostic only."""
    for line in stdout.splitlines():
        if line.startswith(WATCHER_DECISION_PREFIX):
            return line[len(WATCHER_DECISION_PREFIX):].strip() or None
    return None


def execution_files(record: Record) -> list[Path]:
    """The files one execution wrote, exactly as its record names them: the log and, for pytest, the
    JUnit sibling that shares its stem. A record without a log names nothing."""
    if not record.log_path:
        return []
    log = Path(record.log_path)
    return [log, log.with_suffix(".junit.xml")] if log.suffix == ".log" else [log]


def discard_execution_files(record: Record) -> None:
    """Remove the files of an execution the store collapsed as an identical consecutive observation.

    The stored record's own files already hold the equivalent evidence, so keeping these would only
    grow ``data/logs`` with every tick. Failure to remove is a warning, never a verdict.
    """
    for path in execution_files(record):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            try:
                print(f"warning: could not remove {path} from a collapsed execution: {type(exc).__name__}",
                      file=sys.stderr)
            except (OSError, ValueError):
                pass


class Runner:
    def __init__(self, mirrors: Mirrors, cache: Path, logs: Path, gh: GitHub, catalogue: dict[str, Any]):
        self.mirrors = mirrors
        self.cache = Path(cache)
        self.logs = Path(logs)
        self.gh = gh
        self.cat = catalogue
        self.logs.mkdir(parents=True, exist_ok=True)

    def _source(
        self, source_type: str, check_id: str, check: dict[str, Any],
        condition_ids: list[str], **extra: Any,
    ) -> dict[str, Any]:
        return {
            "type": source_type,
            "check": check_id,
            "check_fingerprint": check_fingerprint(check),
            "condition_fingerprints": condition_fingerprint_map(self.cat, condition_ids),
            **extra,
        }

    # ---- trees & environments --------------------------------------------------------

    def _execution_paths(self, check_id: str, key: str) -> tuple[Path, Path]:
        """Fresh log and JUnit names for one execution, carrying its start instant. A stored record's
        files are never reused: if the names exist, the stem is suffixed rather than overwritten."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        base = stem = f"{check_id}.{key}.{stamp}"
        counter = 0
        while (self.logs / f"{stem}.log").exists() or (self.logs / f"{stem}.junit.xml").exists():
            counter += 1
            stem = f"{base}.{counter}"
        return self.logs / f"{stem}.log", self.logs / f"{stem}.junit.xml"

    def tree(self, repo: str, sha: str) -> Failure | Path:
        return self.mirrors.extract(repo, sha, self.cache / "trees" / repo / sha[:12])

    def _venv(self, repo: str, tree: Path, sha: str, extra_trees: list[Path] = ()) -> Failure | Path:
        """An environment per extracted tree, built the way CI builds it: `uv sync`.

        The venv lives inside the tree (.venv), so it is bound to the exact revision. Extra
        trees (a cross-app participant) are installed editable on top.
        """
        if not shutil.which("uv"):
            return Failure("env", "uv not installed")
        venv = tree / ".venv"
        env = sanitized_python_env(UV_PROJECT_ENVIRONMENT=str(venv))
        try:
            r = subprocess.run(uv_sync_command(self.cat["repos"].get(repo, {}).get("python")), cwd=tree,
                               capture_output=True, text=True, timeout=1200, env=env)
        except subprocess.TimeoutExpired:
            return Failure("env", "uv sync timed out after 1200 seconds", {"repo": repo, "sha": sha})
        except OSError as exc:
            return Failure("env", f"uv sync could not start: {type(exc).__name__}", {"repo": repo, "sha": sha})
        if r.returncode != 0:
            return Failure("env", r.stderr[-400:], {"repo": repo, "sha": sha})
        py = venv / "bin" / "python"
        for extra in extra_trees:
            try:
                r = subprocess.run(["uv", "pip", "install", "--quiet", "--python", str(py), "-e", str(extra)],
                                   capture_output=True, text=True, timeout=900,
                                   env=sanitized_python_env())
            except subprocess.TimeoutExpired:
                return Failure("env", "editable install timed out after 900 seconds",
                               {"repo": repo, "sha": sha, "extra": str(extra)})
            except OSError as exc:
                return Failure("env", f"editable install could not start: {type(exc).__name__}",
                               {"repo": repo, "sha": sha, "extra": str(extra)})
            if r.returncode != 0:
                return Failure("env", r.stderr[-400:], {"repo": repo, "sha": sha, "extra": str(extra)})
        return venv

    def _desktop_deps(self, tree: Path) -> Failure | None:
        d = tree / "desktop"
        if not (d / "package.json").exists():
            return None
        lockfile = d / "pnpm-lock.yaml"
        if not lockfile.exists():
            return Failure("env", "desktop/pnpm-lock.yaml missing")
        fingerprint = hashlib.sha256()
        try:
            for dependency_file in (d / "package.json", lockfile, d / "pnpm-workspace.yaml"):
                if dependency_file.exists():
                    fingerprint.update(dependency_file.name.encode())
                    fingerprint.update(dependency_file.read_bytes())
        except OSError as exc:
            return Failure("env", f"desktop dependency inputs could not be read: {type(exc).__name__}",
                           {"directory": str(d)})
        expected = fingerprint.hexdigest()
        marker = d / ".lcstatus-pnpm-ready"
        try:
            if (d / "node_modules").is_dir() and marker.read_text().strip() == expected:
                return None
        except (OSError, UnicodeError):
            pass
        try:
            marker.unlink(missing_ok=True)
        except OSError as exc:
            return Failure("env", f"desktop dependency marker could not be cleared: {type(exc).__name__}",
                           {"directory": str(d)})
        pnpm = find_tool("pnpm")
        if pnpm is None:
            return Failure("env", "pnpm not installed (searched PATH, ~/.nvm/versions/node/*/bin, ~/.local/share/pnpm)")
        try:
            r = subprocess.run([pnpm, "install", "--frozen-lockfile", "--silent"], cwd=d,
                               capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            return Failure("env", "pnpm install timed out after 900 seconds", {"directory": str(d)})
        except OSError as exc:
            return Failure("env", f"pnpm install could not start: {type(exc).__name__}",
                           {"directory": str(d)})
        if r.returncode != 0:
            return Failure("env", r.stderr[-300:])
        try:
            atomic_write(marker, expected + "\n")
        except OSError as exc:
            return Failure("env", f"desktop dependency marker could not be written: {type(exc).__name__}",
                           {"directory": str(d)})
        return None

    # ---- source inspection ----------------------------------------------------------

    def source_inspection(self, check_id: str, check: dict[str, Any], rev: Revision,
                          condition_ids: list[str], task_ids: list[str]) -> Record:
        repo = check["repo"]
        base = dict(kind="source_inspection", repo=repo, revision=rev.sha,
                    revision_time=rev.committed_at, platform=check.get("platform", "n/a"),
                    condition_ids=condition_ids, task_ids=task_ids,
                    source=self._source("source_inspection", check_id, check, condition_ids))
        tree = self.tree(repo, rev.sha)
        if isinstance(tree, Failure):
            return Record(verdict="unavailable", summary=f"{tree.what}: {tree.why}", **base)
        texts: dict[str, str] = {}
        missing: list[str] = []
        for relative in check.get("paths", []):
            source = tree / relative
            if not source.is_file():
                missing.append(relative)
                continue
            texts[relative] = source.read_text(encoding="utf-8", errors="replace")
        hits = {
            label: [relative for relative, content in texts.items() if marker in content]
            for label, marker in check.get("markers", {}).items()
        }
        found = sum(bool(paths) for paths in hits.values())
        total = len(hits)
        summary = f"inspection only: {found}/{total} configured markers found"
        if missing:
            summary += f"; {len(missing)} path(s) missing"
        return Record(verdict="inconclusive", summary=summary,
                      command=f"inspect exact tree: {', '.join(check.get('paths', []))}",
                      detail={"marker_hits": hits, "missing_paths": missing}, **base)

    # ---- pytest ---------------------------------------------------------------------

    def pytest(self, check_id: str, check: dict[str, Any], rev: Revision, condition_ids: list[str], task_ids: list[str]) -> Record:
        repo = check["repo"]
        base = dict(kind="automated_test", repo=repo, revision=rev.sha, revision_time=rev.committed_at,
                    platform=check.get("platform", "linux"), condition_ids=condition_ids, task_ids=task_ids,
                    source=self._source(
                        "local_runner", check_id, check, condition_ids, host=os.uname().nodename,
                    ))
        tree = self.tree(repo, rev.sha)
        if isinstance(tree, Failure):
            return Record(verdict="unavailable", summary=f"{tree.what}: {tree.why}", **base)
        for argument in check.get("args", []):
            target = argument.split("::", 1)[0]
            if not target.startswith("-") and target.endswith(".py") and not (tree / target).is_file():
                return Record(
                    verdict="unavailable",
                    summary=f"configured pytest target absent at this revision: {target}",
                    **base,
                )
        venv = self._venv(repo, tree, rev.sha)
        if isinstance(venv, Failure):
            return Record(verdict="unavailable", summary=f"{venv.what}: {venv.why}", **base)
        if check.get("desktop_deps"):
            f = self._desktop_deps(tree)
            if isinstance(f, Failure):
                return Record(verdict="unavailable", summary=f"desktop deps: {f.why}", **base)
        log, junit = self._execution_paths(check_id, rev.sha[:12])
        # No -q here: a repo whose addopts already has -q would become -qq and lose its
        # summary line. Verdicts never depend on that line; people reading logs do.
        cmd = [str(venv / "bin" / "python"), "-m", "pytest", "-p", "no:cacheprovider",
               f"--junitxml={junit}", *check.get("args", [])]
        # The JUnit path is per execution and `command` is part of record identity, so the recorded
        # command names what ran; where this execution's artefacts landed is log_path's job.
        recorded = " ".join(arg for arg in cmd if not arg.startswith("--junitxml="))
        t0 = time.time()
        try:
            r = subprocess.run(
                cmd, cwd=tree, capture_output=True, text=True, timeout=3600,
                env=sanitized_python_env(),
            )
        except subprocess.TimeoutExpired:
            return Record(verdict="unavailable", summary="timeout", command=recorded, **base)
        except OSError as exc:
            return Record(verdict="unavailable", summary=f"could not start: {type(exc).__name__}",
                          command=recorded, **base)
        dur = round(time.time() - t0, 1)
        log.write_text(r.stdout + "\n--- stderr ---\n" + r.stderr, encoding="utf-8")
        executed = failed = skipped = None
        if junit.exists():
            try:
                root = ET.parse(junit).getroot()
                suites = root.findall(".//testsuite") or [root]
                tests = sum(int(s.get("tests", 0)) for s in suites)
                failures = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
                skips = sum(int(s.get("skipped", 0)) for s in suites)
                executed, failed, skipped = tests - skips, failures, skips
            except ET.ParseError:
                pass
        summary = next((l for l in reversed(r.stdout.splitlines()) if " passed" in l or " failed" in l or " error" in l), "").strip() or None
        # The verdict is decided by exit code and counts, never by the summary line.
        if r.returncode != 0:
            verdict = "fail"
        elif executed is None:
            verdict = "unknown"
        elif executed == 0:
            verdict = "skip"
        elif failed:
            verdict = "fail"
        else:
            verdict = "pass"
        return Record(verdict=verdict, command=f"[{interpreter_version(venv)}] {recorded}", exit_code=r.returncode, executed=executed,
                      failed=failed, skipped=skipped, summary=summary, log_path=str(log), duration_s=dur, **base)

    # ---- cargo (Document Summarizer) -------------------------------------------------

    def cargo_lib(self, check_id: str, check: dict[str, Any], rev: Revision, condition_ids: list[str], task_ids: list[str]) -> Record:
        repo = check["repo"]
        base = dict(kind="automated_test", repo=repo, revision=rev.sha, revision_time=rev.committed_at,
                    platform="linux", condition_ids=condition_ids, task_ids=task_ids,
                    source=self._source(
                        "local_runner", check_id, check, condition_ids, host=os.uname().nodename,
                    ))
        tree = self.tree(repo, rev.sha)
        if isinstance(tree, Failure):
            return Record(verdict="unavailable", summary=f"{tree.what}: {tree.why}", **base)
        for tool in ("npm", "cargo"):
            if not shutil.which(tool):
                return Record(verdict="unavailable", summary=f"{tool} not installed", **base)
        log, _ = self._execution_paths(check_id, rev.sha[:12])
        t0 = time.time()
        steps = [(["npm", "install", "--silent"], tree), (["npm", "run", "build"], tree),
                 (["cargo", "test", "--lib"], tree / "src-tauri")]
        out: list[str] = []
        rc = 0
        for cmd, cwd in steps:
            try:
                r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=3600)
            except subprocess.TimeoutExpired:
                log.write_text("\n".join(out), encoding="utf-8")
                return Record(verdict="unavailable", summary=f"timeout in {' '.join(cmd)}", log_path=str(log), **base)
            except OSError as exc:
                log.write_text("\n".join(out), encoding="utf-8")
                return Record(verdict="unavailable",
                              summary=f"could not start {' '.join(cmd)}: {type(exc).__name__}",
                              log_path=str(log), **base)
            out += [f"$ {' '.join(cmd)}", r.stdout, r.stderr]
            rc = r.returncode
            if rc != 0:
                break
        log.write_text("\n".join(out), encoding="utf-8")
        text = "\n".join(out)
        summary = next((l for l in reversed(text.splitlines()) if l.startswith("test result:")), None)
        executed = failed = None
        if summary:
            import re
            m = re.search(r"(\d+) passed; (\d+) failed; (\d+) ignored", summary)
            if m:
                executed = int(m.group(1)) + int(m.group(2)); failed = int(m.group(2))
        verdict = "fail" if rc != 0 else ("unknown" if executed is None else ("skip" if executed == 0 else ("fail" if failed else "pass")))
        return Record(verdict=verdict, command="npm install && npm run build && cargo test --lib", exit_code=rc,
                      executed=executed, failed=failed, summary=summary, log_path=str(log),
                      duration_s=round(time.time() - t0, 1), **base)

    # ---- cross-app acceptance (Email Watcher -> Invoice Processor) --------------------

    def accept_ew_ip(self, check_id: str, check: dict[str, Any], revs: dict[str, Revision], condition_ids: list[str], task_ids: list[str]) -> Record:
        participants = {repo: revs[repo].sha for repo in declared_participants(check)}
        ip, ew = revs["invoice-processor"], revs["eom-email-watcher"]
        base = dict(kind="automated_test", repo="invoice-processor", revision=ip.sha,
                    revision_time=ip.committed_at, platform="linux", condition_ids=condition_ids,
                    task_ids=task_ids, participants=participants,
                    source=self._source(
                        "local_runner", check_id, check, condition_ids, host=os.uname().nodename,
                    ))
        # The products only honour a licence signed by their production authority, so the run
        # borrows the operator's installed one. Without a usable licence the acceptance cannot run.
        licence_path = installed_entitlement_path(os.environ)
        if isinstance(licence_path, Failure):
            return Record(verdict="unavailable",
                          summary=f"installed Connect entitlement unreadable: {licence_path.why}", **base)
        licence = read_installed_entitlement(licence_path)
        if isinstance(licence, Failure):
            return Record(verdict="unavailable", summary=licence.why, **base)
        licence_bytes, entitlement = licence
        trees: dict[str, Path] = {}
        for repo in declared_participants(check):
            tree = self.tree(repo, revs[repo].sha)
            if isinstance(tree, Failure):
                return Record(verdict="unavailable", summary=f"could not extract {repo}: {tree.why}", **base)
            trees[repo] = tree
        ip_tree = trees["invoice-processor"]
        ew_tree = trees["eom-email-watcher"]
        contracts_tree = trees["connect-contracts"]
        venv = self._venv("invoice-processor", ip_tree, ip.sha, extra_trees=[ew_tree])
        if isinstance(venv, Failure):
            return Record(verdict="unavailable", summary=f"env: {venv.why}", **base)
        script = ip_tree / "scripts" / "accept_against_email_watcher.py"
        if not script.exists():
            return Record(verdict="unavailable", summary="acceptance script absent at this revision", **base)

        key = f"{ip.sha[:12]}-{ew.sha[:12]}-{revs['connect-contracts'].sha[:12]}"
        compat_home = self.cache / "xapp-homes" / key
        if compat_home.is_symlink():
            compat_home.unlink()
        elif compat_home.exists():
            shutil.rmtree(compat_home)
        desktop = compat_home / "Desktop"
        desktop.mkdir(parents=True)
        (desktop / "invoice-processor").symlink_to(ip_tree, target_is_directory=True)
        (desktop / "connect-contracts").symlink_to(contracts_tree, target_is_directory=True)

        # The upstream script hardcodes this compatibility path. Serialize its use and refuse
        # to remove any directory or foreign symlink already present there.
        wm = WATCHER_COMPAT_PATH
        lock_fh = open(WATCHER_COMPAT_LOCK, "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_fh.close()
            return Record(verdict="unavailable", summary="watcher compatibility path is busy", **base)
        linked = False
        staged: Path | None = None
        try:
            failure = prepare_owned_symlink(
                wm, ew_tree, self.cache / "trees" / "eom-email-watcher"
            )
            if failure is not None:
                return Record(verdict="unavailable", summary=failure.why, **base)
            linked = True
            # Fix the cleanup path before the copy starts, so a copy that fails half-way is still removed.
            staged = compat_home / ".config" / INSTALLED_ENTITLEMENT
            try:
                stage_entitlement(compat_home, licence_bytes)
            except OSError as exc:
                return Record(verdict="unavailable",
                              summary=f"installed Connect entitlement unreadable: could not stage ({type(exc).__name__})",
                              **base)
            log, _ = self._execution_paths(check_id, f"{ip.sha[:8]}-{ew.sha[:8]}")
            env = sanitized_python_env(
                HOME=str(compat_home),
                PYTHONPATH=str(ew_tree / "src"),
                ACCEPTANCE_DIR=str(self.cache / "xapp-runs" / key),
                **isolated_xdg_env(compat_home),
            )
            env.pop("ACCEPTANCE_MODEL", None)
            wrapper = (
                "import sys, runpy\n"
                f"sys.path.insert(0, {str(ip_tree)!r})\n"
                "import tests.fixtures, tests.oracle\n"
                f"ok = tests.fixtures.__file__.startswith({str(ip_tree)!r}) and tests.oracle.__file__.startswith({str(ip_tree)!r})\n"
                "if not ok:\n"
                "    print('ISOLATION FAILURE: fixtures resolved outside the extracted tree', file=sys.stderr); sys.exit(97)\n"
                f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
            )
            cmd = [str(venv / "bin" / "python"), "-c", wrapper]
            t0 = time.time()
            try:
                r = subprocess.run(cmd, cwd=ip_tree, capture_output=True, text=True,
                                   timeout=1800, env=env)
            except subprocess.TimeoutExpired:
                return Record(verdict="unavailable", summary="timeout",
                              command="accept_against_email_watcher.py (isolated)", **base)
            except OSError as exc:
                return Record(verdict="unavailable", summary=f"could not start: {type(exc).__name__}",
                              command="accept_against_email_watcher.py (isolated)", **base)
            log.write_text(r.stdout + "\n--- stderr ---\n" + r.stderr, encoding="utf-8")
            last = (r.stdout.strip().splitlines() or [""])[-1]
            if r.returncode == 97:
                return Record(verdict="unavailable", summary="isolation failure: fixtures from outside the extracted tree",
                              command="accept_against_email_watcher.py (isolated)", exit_code=97,
                              log_path=str(log), **base)
            proof = invoice_acceptance_proof(r.stdout)
            verdict = "pass" if r.returncode == 0 and proof is not None else "fail"
            decision = watcher_decision(r.stdout)
            return Record(verdict=verdict,
                          command="accept_against_email_watcher.py (isolated, stand-in model)",
                          exit_code=r.returncode,
                          summary=(last[:200] if proof is not None or r.returncode != 0
                                   else "acceptance process exited zero without complete proof"),
                          log_path=str(log),
                          duration_s=round(time.time() - t0, 1),
                          detail={"fixtures_pinned_to": str(ip_tree),
                                  "watcher_tree": str(ew_tree),
                                  "contracts_tree": str(contracts_tree),
                                  "isolated_home": str(compat_home),
                                  **entitlement,
                                  **({"acceptance_proof": proof} if proof is not None else {}),
                                  **({"watcher_decision": decision} if decision else {})}, **base)
        finally:
            if staged is not None:
                try:
                    staged.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    try:   # a warning must never abort the cleanup that follows it
                        print(f"warning: staged entitlement left behind at {staged}: {type(exc).__name__}",
                              file=sys.stderr)
                    except (OSError, ValueError):
                        pass
            if linked and wm.is_symlink() and wm.resolve(strict=False) == ew_tree.resolve():
                wm.unlink()
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

    # ---- cross-app acceptance (Email Watcher -> Document Summarizer) -----------------

    def accept_ew_ds(self, check_id: str, check: dict[str, Any], revs: dict[str, Revision], condition_ids: list[str], task_ids: list[str]) -> Record:
        participants = {repo: revs[repo].sha for repo in declared_participants(check)}
        ew, ds = revs["eom-email-watcher"], revs["document-summarizer"]
        base = dict(kind="automated_test", repo="eom-email-watcher", revision=ew.sha,
                    revision_time=ew.committed_at, platform="linux", condition_ids=condition_ids,
                    task_ids=task_ids, participants=participants,
                    source=self._source(
                        "local_runner", check_id, check, condition_ids, host=os.uname().nodename,
                    ))
        trees: dict[str, Path] = {}
        for repo in declared_participants(check):
            tree = self.tree(repo, revs[repo].sha)
            if isinstance(tree, Failure):
                return Record(verdict="unavailable", summary=f"could not extract {repo}: {tree.why}", **base)
            trees[repo] = tree

        ew_tree = trees["eom-email-watcher"]
        ds_tree = trees["document-summarizer"]
        contracts_tree = trees["connect-contracts"]
        proof = ew_tree / "scripts" / "connect-local-proof.py"
        pdf = ds_tree / "src-tauri" / "tests" / "fixtures" / "structured_report.pdf"
        keyring = contracts_tree / "entitlements" / "v1" / "fixtures" / "test-keyring.json"
        active = contracts_tree / "entitlements" / "v1" / "fixtures" / "valid" / "active.json"
        expired = contracts_tree / "entitlements" / "v1" / "fixtures" / "valid" / "expired.json"
        contract_requirements = contracts_tree / "requirements-dev.txt"
        required = (proof, pdf, keyring, active, expired, contract_requirements)
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            return Record(verdict="unavailable", summary=f"cross-app proof input absent: {missing[0]}", **base)

        npm = find_tool("npm")
        cargo = find_tool("cargo")
        uv = find_tool("uv")
        xvfb = find_tool("xvfb-run")
        absent_tool = next((name for name, path in (("npm", npm), ("cargo", cargo), ("uv", uv), ("xvfb-run", xvfb)) if path is None), None)
        if absent_tool:
            return Record(verdict="unavailable", summary=f"{absent_tool} not installed", **base)
        venv = self._venv("eom-email-watcher", ew_tree, ew.sha)
        if isinstance(venv, Failure):
            return Record(verdict="unavailable", summary=f"env: {venv.why}", **base)

        key = f"{ew.sha[:12]}-{ds.sha[:12]}-{revs['connect-contracts'].sha[:12]}"
        log, _ = self._execution_paths(check_id, key)
        env = sanitized_python_env(
            LOCAL_CONNECT_ENTITLEMENT_KEYRING_FILE=str(keyring),
            LIBGL_ALWAYS_SOFTWARE="1",
            WEBKIT_DISABLE_COMPOSITING_MODE="1",
            WEBKIT_DISABLE_DMABUF_RENDERER="1",
        )
        tool_dirs = [str(Path(path).parent) for path in (npm, cargo, uv, xvfb)]
        env["PATH"] = os.pathsep.join([*dict.fromkeys(tool_dirs), env.get("PATH", "")])
        steps = [
            ([uv, "run", "--quiet", "--with-requirements", str(contract_requirements),
              "python", "-m", "unittest", "discover", "-s", "tests"], contracts_tree, 1200),
            ([npm, "install", "--silent"], ds_tree, 1200),
            ([npm, "run", "desktop:build:no-bundle"], ds_tree, 3600),
        ]
        output: list[str] = []
        started = time.time()
        for cmd, cwd, timeout in steps:
            try:
                result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                        timeout=timeout, env=env)
            except subprocess.TimeoutExpired:
                log.write_text("\n".join(output), encoding="utf-8")
                return Record(verdict="unavailable", summary=f"timeout in {' '.join(cmd)}",
                              command=" && ".join(" ".join(step[0]) for step in steps),
                              log_path=str(log), **base)
            except OSError as exc:
                log.write_text("\n".join(output), encoding="utf-8")
                return Record(verdict="unavailable", summary=f"could not start {' '.join(cmd)}: {type(exc).__name__}",
                              command=" && ".join(" ".join(step[0]) for step in steps),
                              log_path=str(log), **base)
            output += [f"$ {' '.join(cmd)}", result.stdout, result.stderr]
            if result.returncode != 0:
                log.write_text("\n".join(output), encoding="utf-8")
                summary = next((line for line in reversed((result.stdout + result.stderr).splitlines()) if line.strip()), "build failed")
                return Record(verdict="fail", summary=summary[:200], exit_code=result.returncode,
                              command=" ".join(cmd), log_path=str(log),
                              duration_s=round(time.time() - started, 1), **base)

        provider = ds_tree / "src-tauri" / "target" / "release" / "document-summarizer"
        if not provider.is_file():
            log.write_text("\n".join(output), encoding="utf-8")
            return Record(verdict="unavailable", summary="Document Summarizer build produced no release binary",
                          log_path=str(log), **base)
        cmd = [xvfb, "-a", str(venv / "bin" / "python"), str(proof),
               "--provider-binary", str(provider), "--pdf", str(pdf),
               "--entitlement-keyring", str(keyring), "--active-entitlement", str(active),
               "--expired-entitlement", str(expired)]
        try:
            result = subprocess.run(cmd, cwd=ew_tree, capture_output=True, text=True,
                                    timeout=1800, env=env)
        except subprocess.TimeoutExpired:
            log.write_text("\n".join(output), encoding="utf-8")
            return Record(verdict="unavailable", summary="cross-app proof timed out after 1800 seconds",
                          command="connect-local-proof.py (exact trees, stand-in model)",
                          log_path=str(log), **base)
        except OSError as exc:
            log.write_text("\n".join(output), encoding="utf-8")
            return Record(verdict="unavailable", summary=f"could not start cross-app proof: {type(exc).__name__}",
                          command="connect-local-proof.py (exact trees, stand-in model)",
                          log_path=str(log), **base)
        output += [f"$ {' '.join(cmd)}", result.stdout, result.stderr]
        log.write_text("\n".join(output), encoding="utf-8")
        summary = next((line for line in reversed((result.stdout + result.stderr).splitlines()) if line.strip()), "cross-app proof completed")
        return Record(verdict="pass" if result.returncode == 0 else "fail",
                      command="connect-local-proof.py (exact trees, stand-in model, software rendering)",
                      exit_code=result.returncode, executed=1, failed=int(result.returncode != 0),
                      summary=summary[:200], log_path=str(log),
                      duration_s=round(time.time() - started, 1),
                      detail={"email_watcher_tree": str(ew_tree),
                              "document_summarizer_tree": str(ds_tree),
                              "contracts_tree": str(contracts_tree),
                              "provider_binary": str(provider)}, **base)

    # ---- GitHub Actions --------------------------------------------------------------

    def release_issues(
        self, check_id: str, check: dict[str, Any], rev: Revision,
        condition_ids: list[str], task_ids: list[str],
    ) -> Record:
        """Record the exact open-issue gate for one repository and release milestone.

        Contract 05: the gate can read clear only after the milestone was found in the repository,
        the listing succeeded, and the listing agreed with the milestone's own open count.  Every
        other outcome is unavailable, never pass (nothing confirmed the gate) and never fail (a fail
        would name blockers the evidence does not contain).
        """
        repo = check["repo"]
        milestone = check["milestone"]
        gh_repo = self.cat["repos"][repo]["github"]
        base = dict(
            kind="issue_gate", repo=repo, revision=rev.sha,
            revision_time=rev.committed_at, platform="n/a",
            condition_ids=condition_ids, task_ids=task_ids,
            source=self._source("github_issues", check_id, check, condition_ids),
        )
        detail: dict[str, Any] = {"milestone": milestone}

        def unavailable(summary: str, **extra: Any) -> Record:
            return Record(verdict="unavailable", summary=summary, detail={**detail, **extra}, **base)

        listed = self.gh.milestones(gh_repo)
        if isinstance(listed, Failure):
            return unavailable(f"{listed.what}: {listed.why}")
        if not isinstance(listed, list):
            return unavailable("GitHub milestones response was not a list")
        seen: list[str] = []
        matches: list[dict[str, Any]] = []
        for item in listed:
            if (
                not isinstance(item, dict) or not isinstance(item.get("title"), str)
                or type(item.get("number")) is not int or item.get("state") not in ("open", "closed")
                or type(item.get("open_issues")) is not int
            ):
                return unavailable("GitHub milestones response contained a malformed milestone")
            seen.append(item["title"])
            if item["title"] == milestone:
                matches.append(item)
        if not matches:
            return unavailable(f"milestone not found: {milestone}", milestones_seen=sorted(seen))
        if len(matches) > 1:
            return unavailable(f"GitHub returned {len(matches)} milestones titled {milestone}")
        found = matches[0]
        detail.update(milestone_number=found["number"], milestone_state=found["state"],
                      milestone_open_issues=found["open_issues"])

        items = self.gh.open_issues_and_pulls(gh_repo)
        if isinstance(items, Failure):
            return unavailable(f"{items.what}: {items.why}")
        if not isinstance(items, list):
            return unavailable("GitHub issues response was not a list")

        blockers: list[dict[str, Any]] = []
        in_milestone = 0
        for item in items:
            if not isinstance(item, dict):
                return unavailable("GitHub issues response contained a malformed issue")
            item_milestone = item.get("milestone")
            if item_milestone is None:
                continue
            if not isinstance(item_milestone, dict) or not isinstance(item_milestone.get("title"), str):
                return unavailable("GitHub issues response contained a malformed milestone")
            if item_milestone["title"] != milestone:
                continue
            in_milestone += 1                      # GitHub's open_issues counts pull requests too
            if "pull_request" in item:
                continue                           # a pull request is not a blocker
            number, title = item.get("number"), item.get("title")
            raw_labels = item.get("labels", [])
            if (
                not isinstance(number, int) or number <= 0
                or not isinstance(title, str) or not title.strip()
                or not isinstance(raw_labels, list)
                or any(not isinstance(label, dict) for label in raw_labels)
            ):
                return unavailable("GitHub issues response contained a malformed release issue")
            blockers.append({
                "repo": repo,
                "number": number,
                "title": title.strip(),
                "url": f"https://github.com/{gh_repo}/issues/{number}",
                "labels": sorted(
                    label["name"] for label in raw_labels
                    if isinstance(label.get("name"), str) and label["name"]
                ),
            })
        if in_milestone != found["open_issues"]:
            return unavailable(
                f"issue listing disagrees with milestone count ({in_milestone} listed, {found['open_issues']} reported)"
            )
        blockers.sort(key=lambda issue: issue["number"])
        count = len(blockers)
        return Record(
            verdict="fail" if blockers else "pass",
            summary=f"{count} open issue{'s' if count != 1 else ''} in {milestone}",
            detail={**detail, "issues": blockers},
            **base,
        )

    def ci_jobs(self, repo: str, rev: Revision, checks: dict[str, dict[str, Any]], cond_map: dict[str, tuple[list[str], list[str]]]) -> list[Record]:
        """Record every CI job for this exact sha that a check references. Missing jobs stay 'not checked'."""
        gh_repo = self.cat["repos"][repo]["github"]
        out: list[Record] = []
        runs = self.gh.runs_for_sha(gh_repo, rev.sha)
        if isinstance(runs, Failure):
            for cid, chk in checks.items():
                conds, tasks = cond_map.get(cid, ([], []))
                out.append(Record(kind="ci_run", repo=repo, revision=rev.sha, revision_time=rev.committed_at, verdict="unavailable",
                                  platform=chk.get("platform", "n/a"), condition_ids=conds, task_ids=tasks,
                                  source=self._source("github_actions", cid, chk, conds),
                                  summary=f"{runs.what}: {runs.why}"))
            return out
        by_workflow = latest_workflow_runs(runs)
        for cid, chk in checks.items():
            conds, tasks = cond_map.get(cid, ([], []))
            run = by_workflow.get(chk["workflow"])
            src = self._source(
                "github_actions", cid, chk, conds,
                workflow=chk["workflow"], job=chk["job"],
            )
            if run is None:
                out.append(Record(kind="ci_run", repo=repo, revision=rev.sha, revision_time=rev.committed_at, verdict="unknown",
                                  platform=chk.get("platform", "n/a"), condition_ids=conds, task_ids=tasks, source=src,
                                  summary=f"no '{chk['workflow']}' run for this revision"))
                continue
            jobs = self.gh.jobs_for_run(gh_repo, run["id"])
            if isinstance(jobs, Failure):
                out.append(Record(kind="ci_run", repo=repo, revision=rev.sha, revision_time=rev.committed_at, verdict="unavailable",
                                  platform=chk.get("platform", "n/a"), condition_ids=conds, task_ids=tasks, source=src, summary=jobs.why))
                continue
            job = next((j for j in jobs if j.get("name") == chk["job"]), None)
            if job is None:
                out.append(Record(kind="ci_run", repo=repo, revision=rev.sha, revision_time=rev.committed_at, verdict="unknown",
                                  platform=chk.get("platform", "n/a"), condition_ids=conds, task_ids=tasks, source=src,
                                  summary=f"job '{chk['job']}' not present in run {run['id']}"))
                continue
            labels = [l.lower() for l in job.get("labels", [])]
            platform = "windows" if any("windows" in l for l in labels) else ("macos" if any("macos" in l for l in labels) else "linux")
            concl = job.get("conclusion")
            status = job.get("status")
            if status != "completed":
                verdict = "pending"
            elif concl == "success":
                verdict = "pass"
            elif concl in ("failure", "timed_out"):
                verdict = "fail"
            elif concl in ("cancelled", "skipped"):
                verdict = "skip"
            else:
                verdict = "unknown"
            steps = [s for s in job.get("steps", []) if s.get("conclusion") not in (None, "skipped")]
            src2 = dict(src, run_id=run["id"], job_id=job.get("id"), url=job.get("html_url"), run_attempt=run.get("run_attempt"))
            out.append(Record(kind="ci_run", repo=repo, revision=rev.sha, revision_time=rev.committed_at, verdict=verdict,
                              platform=platform, condition_ids=conds, task_ids=tasks, source=src2,
                              summary=f"{chk['workflow']} / {chk['job']}: {status}/{concl}", executed=len(steps),
                              detail={"completed_at": job.get("completed_at"), "runner": job.get("runner_name")}))
        return out

    # ---- releases --------------------------------------------------------------------

    def releases(self, check_id: str, check: dict[str, Any], repo: str, rev: Revision,
                 condition_ids: list[str], task_ids: list[str]) -> Record:
        """Release evidence for ONE repository.

        Passes only when a published (non-draft, non-prerelease) release exists whose assets
        match every required pattern, and the record's revision is the commit that release
        actually contains, never the current head. So a release of older code reads
        "changed since verification" as soon as the default branch moves on.
        """
        gh_repo = self.cat["repos"][repo]["github"]
        rel = self.gh.releases(gh_repo)
        base = dict(kind="release_artifact", repo=repo, platform="n/a",
                    condition_ids=condition_ids, task_ids=task_ids,
                    source=self._source("github_releases", check_id, check, condition_ids))
        if isinstance(rel, Failure):
            return Record(verdict="unavailable", revision=rev.sha, revision_time=rev.committed_at,
                          summary=f"{rel.what}: {rel.why}", **base)
        requirements = check.get("required_assets", {})
        latest, matches, preliminary = _release_assets(rel, requirements)
        checksum_contents: dict[str, str] = {}
        if latest is not None and not preliminary.get("missing"):
            checksum_labels = [label for label in requirements if "checksum" in label.lower()]
            for label in checksum_labels:
                for asset in matches[label]:
                    asset_id = asset.get("id")
                    if asset_id is None:
                        return Record(verdict="unavailable", revision=rev.sha, revision_time=rev.committed_at,
                                      summary=f"release {latest.get('tag_name')} checksum asset has no API id",
                                      detail=preliminary, **base)
                    content = self.gh.release_asset_text(gh_repo, asset_id)
                    if isinstance(content, Failure):
                        return Record(verdict="unavailable", revision=rev.sha, revision_time=rev.committed_at,
                                      summary=f"release {latest.get('tag_name')} checksum could not be read: {content.why}",
                                      detail=preliminary, **base)
                    checksum_contents[_asset_key(asset)] = content
        verdict, summary, detail, tag = release_verdict(rel, requirements, checksum_contents)
        if verdict != "pass":
            return Record(verdict=verdict, revision=rev.sha, revision_time=rev.committed_at, summary=summary,
                          detail=detail, **base)
        target = self.gh.tag_commit(gh_repo, tag)
        if isinstance(target, Failure):
            return Record(verdict="unavailable", revision=rev.sha, revision_time=rev.committed_at,
                          summary=f"release {tag} found but its tag could not be resolved: {target.why}",
                          detail=detail, **base)
        target_time = self.mirrors.commit_time(repo, target)
        if target_time is None:
            return Record(verdict="unavailable", revision=target, revision_time=None,
                          summary=f"release {tag} found but its commit time could not be read",
                          detail=detail, **base)
        return Record(verdict="pass", revision=target, revision_time=target_time, summary=summary,
                      detail=detail, **base)
