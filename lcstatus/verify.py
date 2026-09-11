"""Runners. Each produces Records with exact revisions, exit codes, and real counts.

Principles that keep these honest:

* Tests run against a tree extracted at the exact revision, never a developer checkout.
* pytest results come from JUnit XML, so "executed / failed / skipped" are counted, not
  parsed from a summary line. A nonzero exit is a failure even if a passing-looking line
  was printed. Zero executed tests is `skip`, never `pass`.
* A GitHub job is recorded as `ci_run` with the job's own conclusion and the platform read
  from the job, not assumed. Pending and cancelled stay pending and skip.
* A runner that cannot run records `unavailable` with the reason. It never returns nothing.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .evidence import Record, atomic_write, now_iso
from .sources import Failure, GitHub, Mirrors, Revision


WATCHER_COMPAT_PATH = Path("/tmp/watcher-main")
WATCHER_COMPAT_LOCK = Path("/tmp/local-connect-status-watcher-main.lock")


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


def interpreter_version(venv: Path) -> str:
    try:
        r = subprocess.run([str(venv / "bin" / "python"), "--version"], capture_output=True, text=True, timeout=30)
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
    asset, and the checksum assets cover the required installer filenames.
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


class Runner:
    def __init__(self, mirrors: Mirrors, cache: Path, logs: Path, gh: GitHub, catalogue: dict[str, Any]):
        self.mirrors = mirrors
        self.cache = Path(cache)
        self.logs = Path(logs)
        self.gh = gh
        self.cat = catalogue
        self.logs.mkdir(parents=True, exist_ok=True)

    # ---- trees & environments --------------------------------------------------------

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
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv))
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
                                   capture_output=True, text=True, timeout=900)
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
                    source={"type": "source_inspection", "check": check_id})
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
                    source={"type": "local_runner", "check": check_id, "host": os.uname().nodename})
        tree = self.tree(repo, rev.sha)
        if isinstance(tree, Failure):
            return Record(verdict="unavailable", summary=f"{tree.what}: {tree.why}", **base)
        venv = self._venv(repo, tree, rev.sha)
        if isinstance(venv, Failure):
            return Record(verdict="unavailable", summary=f"{venv.what}: {venv.why}", **base)
        if check.get("desktop_deps"):
            f = self._desktop_deps(tree)
            if isinstance(f, Failure):
                return Record(verdict="unavailable", summary=f"desktop deps: {f.why}", **base)
        junit = self.logs / f"{check_id}.{rev.sha[:12]}.junit.xml"
        log = self.logs / f"{check_id}.{rev.sha[:12]}.log"
        # No -q here: a repo whose addopts already has -q would become -qq and lose its
        # summary line. Verdicts never depend on that line; people reading logs do.
        cmd = [str(venv / "bin" / "python"), "-m", "pytest", "-p", "no:cacheprovider",
               f"--junitxml={junit}", *check.get("args", [])]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=tree, capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired:
            return Record(verdict="unavailable", summary="timeout", command=" ".join(cmd), **base)
        except OSError as exc:
            return Record(verdict="unavailable", summary=f"could not start: {type(exc).__name__}",
                          command=" ".join(cmd), **base)
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
        return Record(verdict=verdict, command=f"[{interpreter_version(venv)}] " + " ".join(cmd), exit_code=r.returncode, executed=executed,
                      failed=failed, skipped=skipped, summary=summary, log_path=str(log), duration_s=dur, **base)

    # ---- cargo (Document Summarizer) -------------------------------------------------

    def cargo_lib(self, check_id: str, check: dict[str, Any], rev: Revision, condition_ids: list[str], task_ids: list[str]) -> Record:
        repo = check["repo"]
        base = dict(kind="automated_test", repo=repo, revision=rev.sha, revision_time=rev.committed_at,
                    platform="linux", condition_ids=condition_ids, task_ids=task_ids,
                    source={"type": "local_runner", "check": check_id, "host": os.uname().nodename})
        tree = self.tree(repo, rev.sha)
        if isinstance(tree, Failure):
            return Record(verdict="unavailable", summary=f"{tree.what}: {tree.why}", **base)
        for tool in ("npm", "cargo"):
            if not shutil.which(tool):
                return Record(verdict="unavailable", summary=f"{tool} not installed", **base)
        log = self.logs / f"{check_id}.{rev.sha[:12]}.log"
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
        participants = {repo: revs[repo].sha for repo in check["participants"]}
        ip, ew = revs["invoice-processor"], revs["eom-email-watcher"]
        base = dict(kind="automated_test", repo="invoice-processor", revision=ip.sha,
                    revision_time=ip.committed_at, platform="linux", condition_ids=condition_ids,
                    task_ids=task_ids, participants=participants,
                    source={"type": "local_runner", "check": check_id, "host": os.uname().nodename})
        trees: dict[str, Path] = {}
        for repo in check["participants"]:
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
        try:
            failure = prepare_owned_symlink(
                wm, ew_tree, self.cache / "trees" / "eom-email-watcher"
            )
            if failure is not None:
                return Record(verdict="unavailable", summary=failure.why, **base)
            linked = True
            log = self.logs / f"{check_id}.{ip.sha[:8]}-{ew.sha[:8]}.log"
            env = {k: v for k, v in os.environ.items() if k != "ACCEPTANCE_MODEL"}
            env["HOME"] = str(compat_home)
            env["PYTHONPATH"] = str(ew_tree / "src")
            env["ACCEPTANCE_DIR"] = str(self.cache / "xapp-runs" / key)
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
            verdict = "pass" if r.returncode == 0 else "fail"
            return Record(verdict=verdict,
                          command="accept_against_email_watcher.py (isolated, stand-in model)",
                          exit_code=r.returncode, summary=last[:200], log_path=str(log),
                          duration_s=round(time.time() - t0, 1),
                          detail={"fixtures_pinned_to": str(ip_tree),
                                  "watcher_tree": str(ew_tree),
                                  "contracts_tree": str(contracts_tree),
                                  "isolated_home": str(compat_home)}, **base)
        finally:
            if linked and wm.is_symlink() and wm.resolve(strict=False) == ew_tree.resolve():
                wm.unlink()
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

    # ---- GitHub Actions --------------------------------------------------------------

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
                                  source={"type": "github_actions", "check": cid}, summary=f"{runs.what}: {runs.why}"))
            return out
        by_workflow = latest_workflow_runs(runs)
        for cid, chk in checks.items():
            conds, tasks = cond_map.get(cid, ([], []))
            run = by_workflow.get(chk["workflow"])
            src = {"type": "github_actions", "check": cid, "workflow": chk["workflow"], "job": chk["job"]}
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

    def releases(self, check_id: str, repo: str, rev: Revision, condition_ids: list[str], task_ids: list[str],
                 required_assets: dict[str, str] | None = None) -> Record:
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
                    source={"type": "github_releases", "check": check_id})
        if isinstance(rel, Failure):
            return Record(verdict="unavailable", revision=rev.sha, revision_time=rev.committed_at,
                          summary=f"{rel.what}: {rel.why}", **base)
        requirements = required_assets or {}
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
