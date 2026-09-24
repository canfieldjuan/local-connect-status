"""Turn a revision change into "which tasks need looking at again".

A task lists the paths it depends on, per repository. When the default branch moves, the
changed files are matched against those globs. A match invalidates nothing by itself — the
status rules already treat evidence at an older revision as stale — but the change record
names the affected tasks so a reader can see *why* something went from verified to
"changed since verification", and any changed file that no task claims is surfaced as
needing assessment instead of being silently ignored.

Documentation-only changes are called out: they can change what a task promises, but they
cannot prove that anything runs.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

DOC_SUFFIXES = (".md", ".txt", ".rst")
CONTRACT_HINTS = ("docs/contracts/", "docs/CONTRACTS.md", "adr/", "plans/")


def _glob_match(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return path == pattern[:-3] or path.startswith(pattern[:-3] + "/")
    if "**" in pattern:
        head = pattern.split("**", 1)[0]
        return path.startswith(head)
    return fnmatch.fnmatchcase(path, pattern)


@dataclass
class ChangeAssessment:
    repo: str
    old: str
    new: str
    changed_files: list[str]
    affected_tasks: dict[str, list[str]] = field(default_factory=dict)   # task_id -> matching files
    unmapped_files: list[str] = field(default_factory=list)
    docs_only: bool = False
    touches_contract_text: bool = False

    def as_detail(self) -> dict[str, Any]:
        return {
            "changed_files": self.changed_files,
            "affected_tasks": self.affected_tasks,
            "unmapped_files": self.unmapped_files,
            "docs_only": self.docs_only,
            "touches_contract_text": self.touches_contract_text,
        }


def assess(repo: str, old: str, new: str, changed_files: list[str], tasks: list[dict[str, Any]]) -> ChangeAssessment:
    a = ChangeAssessment(repo=repo, old=old, new=new, changed_files=list(changed_files))
    claimed: set[str] = set()
    for task in tasks:
        for dep in task.get("depends_on", []):
            if dep.get("repo") != repo:
                continue
            hits = [f for f in changed_files if any(_glob_match(f, p) for p in dep.get("paths", []))]
            if hits:
                a.affected_tasks.setdefault(task["id"], []).extend(hits)
                claimed.update(hits)
    a.unmapped_files = [f for f in changed_files if f not in claimed]
    a.docs_only = bool(changed_files) and all(f.endswith(DOC_SUFFIXES) for f in changed_files)
    a.touches_contract_text = any(any(h in f for h in CONTRACT_HINTS) for f in changed_files)
    return a


def uncovered_patterns(repo: str, files: list[str], tasks: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The (task, pattern) pairs naming `repo` that match none of `files` (contract 08 B3).

    Same matcher as `assess`, so a pattern reported here is exactly one `assess` can never match
    at this revision: a change to the code it was meant to cover is attributed to no task.
    """
    return [
        (task["id"], pattern)
        for task in tasks
        for dep in task.get("depends_on", [])
        if dep.get("repo") == repo
        for pattern in dep.get("paths", [])
        if not any(_glob_match(f, pattern) for f in files)
    ]
