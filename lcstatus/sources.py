"""Where facts come from: bare git mirrors this collector owns, and the GitHub API.

Every function here returns either data or an explicit `Failure`. Nothing returns an empty
list to mean "could not look": an empty result and a failed lookup are different facts and
the report must be able to tell them apart.

The mirrors are cloned from GitHub into `.cache/mirrors/` and are never a developer's
checkout. Trees for verification are extracted with `git archive` at an exact revision.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


FULL_SHA = re.compile(r"[0-9a-f]{40}")


def is_full_sha(value: Any) -> bool:
    return isinstance(value, str) and FULL_SHA.fullmatch(value) is not None


@dataclass
class Failure:
    what: str
    why: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:  # a Failure is falsy so `if not result` reads naturally
        return False


@dataclass
class Revision:
    repo: str
    sha: str
    committed_at: str
    subject: str


# Paths listed for matching are printed in git's quoted form (contract 08 B3a): bytes outside printable
# ASCII, quotes, backslashes and control characters become a C-style quoted string. The output is ASCII
# whatever the file names are, and the form does not depend on the operator's git configuration.
QUOTED_PATHS = ("-c", "core.quotePath=true")


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 300, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a text command without letting startup/timeout errors escape source adapters."""
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr=f"TimeoutExpired after {timeout} seconds")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=f"{type(exc).__name__}: {exc}")


class Mirrors:
    def __init__(self, root: Path, repos: dict[str, dict[str, Any]]):
        self.root = Path(root)
        self.repos = repos
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, repo: str) -> Path:
        return self.root / f"{repo}.git"

    def ensure(self, repo: str) -> Failure | Path:
        p = self.path(repo)
        if p.exists():
            return p
        url = f"https://github.com/{self.repos[repo]['github']}.git"
        try:
            r = _run(["git", "clone", "--quiet", "--mirror", url, str(p)], timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Failure("clone", f"{type(exc).__name__}", {"repo": repo})
        if r.returncode != 0:
            return Failure("clone", r.stderr.strip()[-400:], {"repo": repo})
        return p

    def fetch(self, repo: str) -> Failure | None:
        """Fetch. A failed fetch is reported, not ignored: the head we read may be stale."""
        p = self.ensure(repo)
        if isinstance(p, Failure):
            return p
        try:
            r = _run(["git", "-C", str(p), "fetch", "--quiet", "--prune", "origin"], timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Failure("fetch", type(exc).__name__, {"repo": repo})
        if r.returncode != 0:
            return Failure("fetch", r.stderr.strip()[-400:], {"repo": repo})
        return None

    def head(self, repo: str, branch: str = "main") -> Failure | Revision:
        p = self.path(repo)
        if not p.exists():
            return Failure("head", "mirror missing", {"repo": repo})
        r = _run(["git", "-C", str(p), "log", "-1", "--format=%H%x00%cI%x00%s", f"refs/heads/{branch}"])
        if r.returncode != 0 or not r.stdout.strip():
            return Failure("head", r.stderr.strip()[-200:] or "no such branch", {"repo": repo, "branch": branch})
        sha, when, subject = r.stdout.strip().split("\x00", 2)
        return Revision(repo, sha, when, subject)

    def commit_time(self, repo: str, sha: str) -> str | None:
        r = _run(["git", "-C", str(self.path(repo)), "log", "-1", "--format=%cI", sha])
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None

    def changed_files(self, repo: str, old: str, new: str) -> Failure | list[str]:
        r = _run(["git", "-C", str(self.path(repo)), *QUOTED_PATHS, "diff", "--name-only", f"{old}..{new}"])
        if r.returncode != 0:
            return Failure("diff", r.stderr.strip()[-200:], {"repo": repo, "old": old, "new": new})
        return [l for l in r.stdout.splitlines() if l.strip()]

    def files_at(self, repo: str, sha: str) -> Failure | list[str]:
        """Every file path in the tree at a revision (contract 08 B3), in the form `changed_files`
        prints them (B3a), so the mapping check sees exactly the paths `assess` matches. A listing
        that fails is a Failure, never an empty list: an empty list would report every pattern as dead."""
        r = _run(["git", "-C", str(self.path(repo)), *QUOTED_PATHS, "ls-tree", "-r", "--name-only", sha])
        if r.returncode != 0:
            return Failure("files", r.stderr.strip()[-200:] or f"exit {r.returncode}", {"repo": repo, "sha": sha})
        return [l for l in r.stdout.splitlines() if l.strip()]

    def commits_between(self, repo: str, old: str, new: str) -> Failure | list[str]:
        r = _run(["git", "-C", str(self.path(repo)), "log", "--format=%h %s", f"{old}..{new}"])
        if r.returncode != 0:
            return Failure("commits", r.stderr.strip()[-200:] or f"exit {r.returncode}",
                           {"repo": repo, "old": old, "new": new})
        return r.stdout.splitlines()

    def show(self, repo: str, sha: str, path: str) -> str | None:
        r = _run(["git", "-C", str(self.path(repo)), "show", f"{sha}:{path}"])
        return r.stdout if r.returncode == 0 else None

    def extract(self, repo: str, sha: str, dest: Path) -> Failure | Path:
        """Extract an exact tree. Idempotent: an existing complete tree is reused."""
        dest = Path(dest)
        marker = dest / ".lcstatus-extracted"
        if marker.exists() and marker.read_text().strip() == sha:
            return dest
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        # bytes, never text: a tar stream is not UTF-8
        try:
            raw = subprocess.run(["git", "-C", str(self.path(repo)), "archive", "--format=tar", sha],
                                 capture_output=True, check=False, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Failure("archive", type(exc).__name__, {"repo": repo, "sha": sha})
        if raw.returncode != 0:
            return Failure("archive", raw.stderr.decode(errors="replace")[-200:], {"repo": repo, "sha": sha})
        with tarfile.open(fileobj=io.BytesIO(raw.stdout), mode="r:") as tf:
            tf.extractall(dest, filter="data")
        marker.write_text(sha)
        return dest


class GitHub:
    """Thin gh wrapper. Every failure is a Failure; pagination is completed or reported."""

    def __init__(self, timeout: int = 120):
        self.timeout = timeout
        self.available = shutil.which("gh") is not None

    def api(self, path: str, *, paginate: bool = False) -> Failure | Any:
        if not self.available:
            return Failure("gh", "gh CLI not installed", {"path": path})
        cmd = ["gh", "api", path]
        if paginate:
            cmd += ["--paginate", "--slurp"]
        try:
            r = _run(cmd, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Failure("gh", type(exc).__name__, {"path": path})
        if r.returncode != 0:
            return Failure("gh", r.stderr.strip()[-300:] or f"exit {r.returncode}", {"path": path})
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            return Failure("gh", "non-JSON response", {"path": path})
        if paginate:
            # --slurp returns a list of pages
            merged: list = []
            for page in data:
                if isinstance(page, list):
                    merged.extend(page)
                elif isinstance(page, dict):
                    for v in page.values():
                        if isinstance(v, list):
                            merged.extend(v)
            return merged
        return data

    def default_branch_head(self, gh_repo: str) -> Failure | dict[str, Any]:
        d = self.api(f"repos/{gh_repo}")
        if isinstance(d, Failure):
            return d
        branch = d.get("default_branch", "main")
        b = self.api(f"repos/{gh_repo}/branches/{branch}")
        if isinstance(b, Failure):
            return b
        c = b.get("commit", {})
        sha = c.get("sha")
        if not is_full_sha(sha):
            return Failure("github_head", "default branch response has no full commit SHA",
                           {"repo": gh_repo, "branch": branch})
        return {"branch": branch, "sha": sha, "committed_at": c.get("commit", {}).get("committer", {}).get("date"),
                "subject": (c.get("commit", {}).get("message") or "").splitlines()[0] if c else ""}

    def runs_for_sha(self, gh_repo: str, sha: str) -> Failure | list[dict[str, Any]]:
        """All workflow runs whose head is exactly this sha (paginated to completion)."""
        runs = self.api(f"repos/{gh_repo}/actions/runs?head_sha={sha}&per_page=100", paginate=True)
        if isinstance(runs, Failure):
            return runs
        return [r for r in runs if isinstance(r, dict) and r.get("head_sha") == sha]

    def jobs_for_run(self, gh_repo: str, run_id: int) -> Failure | list[dict[str, Any]]:
        jobs = self.api(f"repos/{gh_repo}/actions/runs/{run_id}/jobs?per_page=100", paginate=True)
        return jobs

    def releases(self, gh_repo: str) -> Failure | list[dict[str, Any]]:
        return self.api(f"repos/{gh_repo}/releases?per_page=100", paginate=True)

    def release_asset_text(self, gh_repo: str, asset_id: int) -> Failure | str:
        path = f"repos/{gh_repo}/releases/assets/{asset_id}"
        if not self.available:
            return Failure("gh_release_asset", "gh CLI not installed", {"path": path})
        cmd = ["gh", "api", "-H", "Accept: application/octet-stream", path]
        try:
            r = subprocess.run(cmd, capture_output=True, check=False, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Failure("gh_release_asset", type(exc).__name__, {"path": path})
        if r.returncode != 0:
            error = r.stderr.decode(errors="replace").strip()[-300:]
            return Failure("gh_release_asset", error or f"exit {r.returncode}", {"path": path})
        try:
            return r.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return Failure("gh_release_asset", "asset is not UTF-8 checksum text", {"path": path})

    def tag_commit(self, gh_repo: str, tag: str) -> Failure | str:
        """The commit SHA a release tag points at (dereferencing annotated tags)."""
        ref = self.api(f"repos/{gh_repo}/git/ref/tags/{tag}")
        if isinstance(ref, Failure):
            return ref
        obj = ref.get("object") or {}
        if obj.get("type") == "commit" and obj.get("sha"):
            return obj["sha"]
        if obj.get("type") == "tag" and obj.get("sha"):
            t = self.api(f"repos/{gh_repo}/git/tags/{obj['sha']}")
            if isinstance(t, Failure):
                return t
            inner = (t.get("object") or {})
            if inner.get("type") == "commit" and inner.get("sha"):
                return inner["sha"]
        return Failure("github_tag", f"tag {tag} does not resolve to a commit")

    def open_issues_and_pulls(self, gh_repo: str) -> Failure | list[dict[str, Any]]:
        """Every open item of the issues listing, pull requests included. Paginated to completion."""
        return self.api(f"repos/{gh_repo}/issues?state=open&per_page=100", paginate=True)

    def milestones(self, gh_repo: str) -> Failure | list[dict[str, Any]]:
        """Every milestone of the repository, open and closed. Paginated to completion."""
        return self.api(f"repos/{gh_repo}/milestones?state=all&per_page=100", paginate=True)
