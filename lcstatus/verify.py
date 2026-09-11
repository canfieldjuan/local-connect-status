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

import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .evidence import Record, now_iso
from .sources import Failure, GitHub, Mirrors, Revision


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


def release_verdict(releases: list[dict], required_assets: dict[str, str]) -> tuple[str, str, dict, str | None]:
    """Judge a repository's GitHub releases against the catalogue's required assets.

    Returns (verdict, summary, detail, tag). A release counts only if it is published
    (not draft, not prerelease) and every required asset pattern matches at least one
    asset name. "Some release exists" is never proof of the promise.
    """
    published = [r for r in releases if not r.get("draft") and not r.get("prerelease")]
    if not published:
        return "fail", "no published release", {"count": len(releases)}, None
    latest = published[0]
    names = [a.get("name") or "" for a in latest.get("assets", [])]
    missing = [label for label, pattern in required_assets.items()
               if not any(re.search(pattern, n) for n in names)]
    detail = {"tag": latest.get("tag_name"), "published_at": latest.get("published_at"), "assets": names,
              "missing": missing}
    if missing:
        return ("fail", f"{latest.get('tag_name')} published but missing: {', '.join(missing)}", detail,
                latest.get("tag_name"))
    return "pass", f"{latest.get('tag_name')} {latest.get('published_at')}", detail, latest.get("tag_name")


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
        r = subprocess.run(uv_sync_command(self.cat["repos"].get(repo, {}).get("python")), cwd=tree,
                           capture_output=True, text=True, timeout=1200, env=env)
        if r.returncode != 0:
            return Failure("env", r.stderr[-400:], {"repo": repo, "sha": sha})
        py = venv / "bin" / "python"
        for extra in extra_trees:
            r = subprocess.run(["uv", "pip", "install", "--quiet", "--python", str(py), "-e", str(extra)],
                               capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                return Failure("env", r.stderr[-400:], {"repo": repo, "sha": sha, "extra": str(extra)})
        return venv

    def _desktop_deps(self, tree: Path) -> Failure | None:
        d = tree / "desktop"
        if not (d / "package.json").exists():
            return None
        if (d / "node_modules").exists():
            return None
        pnpm = find_tool("pnpm")
        if pnpm is None:
            return Failure("env", "pnpm not installed (searched PATH, ~/.nvm/versions/node/*/bin, ~/.local/share/pnpm)")
        r = subprocess.run([pnpm, "install", "--frozen-lockfile", "--silent"], cwd=d, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            return Failure("env", r.stderr[-300:])
        return None

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
        ip, ew = revs["invoice-processor"], revs["eom-email-watcher"]
        base = dict(kind="automated_test", repo="invoice-processor", revision=ip.sha, revision_time=ip.committed_at,
                    platform="linux", condition_ids=condition_ids, task_ids=task_ids,
                    participants={"invoice-processor": ip.sha, "eom-email-watcher": ew.sha},
                    source={"type": "local_runner", "check": check_id, "host": os.uname().nodename})
        ip_tree = self.tree("invoice-processor", ip.sha)
        ew_tree = self.tree("eom-email-watcher", ew.sha)
        if isinstance(ip_tree, Failure) or isinstance(ew_tree, Failure):
            return Record(verdict="unavailable", summary="could not extract a participant tree", **base)
        # The script hardcodes /tmp/watcher-main for the watcher checkout. Point it at the exact tree.
        wm = Path("/tmp/watcher-main")
        if wm.is_symlink() or wm.exists():
            if wm.is_symlink():
                wm.unlink()
            else:
                shutil.rmtree(wm)
        wm.symlink_to(ew_tree)
        # The environment belongs to Invoice Processor: its interpreter pin applies.
        venv = self._venv("invoice-processor", ip_tree, ip.sha, extra_trees=[ew_tree])
        if isinstance(venv, Failure):
            return Record(verdict="unavailable", summary=f"env: {venv.why}", **base)
        script = ip_tree / "scripts" / "accept_against_email_watcher.py"
        if not script.exists():
            return Record(verdict="unavailable", summary="acceptance script absent at this revision", **base)
        log = self.logs / f"{check_id}.{ip.sha[:8]}-{ew.sha[:8]}.log"
        env = {k: v for k, v in os.environ.items() if k != "ACCEPTANCE_MODEL"}  # stand-in model, never the GPU
        env["PYTHONPATH"] = str(ew_tree / "src")
        env["ACCEPTANCE_DIR"] = str(self.cache / "xapp-run")
        # The script inserts the developer's ~/Desktop/invoice-processor onto sys.path for
        # `tests.fixtures` and `tests.oracle`. Pre-import them from the isolated tree, then
        # assert that is where they came from; if not, this check fails rather than reporting
        # a result about code we did not choose.
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
            r = subprocess.run(cmd, cwd=ip_tree, capture_output=True, text=True, timeout=1800, env=env)
        except subprocess.TimeoutExpired:
            return Record(verdict="unavailable", summary="timeout", command="accept_against_email_watcher.py (isolated)", **base)
        log.write_text(r.stdout + "\n--- stderr ---\n" + r.stderr, encoding="utf-8")
        last = (r.stdout.strip().splitlines() or [""])[-1]
        if r.returncode == 97:
            return Record(verdict="unavailable", summary="isolation failure: fixtures from outside the extracted tree",
                          command="accept_against_email_watcher.py (isolated)", exit_code=97, log_path=str(log), **base)
        verdict = "pass" if r.returncode == 0 else "fail"
        return Record(verdict=verdict, command="accept_against_email_watcher.py (isolated, stand-in model)",
                      exit_code=r.returncode, summary=last[:200], log_path=str(log),
                      duration_s=round(time.time() - t0, 1),
                      detail={"fixtures_pinned_to": str(ip_tree), "watcher_tree": str(ew_tree)}, **base)

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
        by_workflow: dict[str, dict[str, Any]] = {}
        for run in runs:
            name = run.get("name")
            # keep the latest attempt of the latest run per workflow
            if name not in by_workflow or run.get("run_attempt", 0) >= by_workflow[name].get("run_attempt", 0):
                by_workflow[name] = run
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

    def releases(self, repo: str, rev: Revision, condition_ids: list[str], task_ids: list[str],
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
                    condition_ids=condition_ids, task_ids=task_ids, source={"type": "github_releases"})
        if isinstance(rel, Failure):
            return Record(verdict="unavailable", revision=rev.sha, revision_time=rev.committed_at,
                          summary=f"{rel.what}: {rel.why}", **base)
        verdict, summary, detail, tag = release_verdict(rel, required_assets or {})
        if verdict != "pass":
            return Record(verdict=verdict, revision=rev.sha, revision_time=rev.committed_at, summary=summary,
                          detail=detail, **base)
        target = self.gh.tag_commit(gh_repo, tag)
        if isinstance(target, Failure):
            return Record(verdict="unavailable", revision=rev.sha, revision_time=rev.committed_at,
                          summary=f"release {tag} found but its tag could not be resolved: {target.why}",
                          detail=detail, **base)
        return Record(verdict="pass", revision=target, revision_time=detail.get("published_at"), summary=summary,
                      detail=detail, **base)
