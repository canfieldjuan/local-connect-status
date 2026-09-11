"""Durable evidence records.

Every fact the dashboard shows comes from a record here. A record says what was checked,
against which exact revision, on which platform, how it ended, and where the log is. Records
are append-only JSONL; the current report is derived from them, never edited by hand.

Rules the store enforces, because the report's honesty depends on them:

* Consecutive deliveries of the same result are stored once. If a check changes and later
  returns to an earlier result, the recovery is retained as a new observation.
* Ordering for "latest" is by the revision's commit time, then by when the check finished.
  A late result about an old revision can never displace a result about a newer one.
* Verdicts are a closed set. "unavailable", "partial" and "skip" are not "pass" and are
  never collapsed into it downstream.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

KINDS = (
    "revision",             # a default-branch head was observed
    "change",               # files changed between two observed revisions
    "source_inspection",    # a fact read out of the source at a revision (hint, not proof)
    "automated_test",       # a test process this collector ran
    "ci_run",               # a GitHub Actions job result for a revision
    "installed_demo",       # a recorded observation of installed apps working together
    "release_artifact",     # a published release the public can download
    "collection_failure",   # a source could not be read; the absence of data is data
)

VERDICTS = ("pass", "fail", "skip", "unavailable", "pending", "partial", "unknown", "inconclusive")

PLATFORMS = ("linux", "windows", "macos", "n/a")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def instant_key(value: str | None) -> tuple[int, float, str]:
    """Chronological key for aware ISO-8601 timestamps, with a stable invalid fallback."""
    if not value:
        return (0, 0.0, "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return (0, 0.0, value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return (0, 0.0, value)
    return (1, parsed.timestamp(), "")


@dataclass
class Record:
    kind: str
    repo: str
    revision: str                       # full sha the evidence is ABOUT
    verdict: str
    platform: str = "n/a"
    task_ids: list[str] = field(default_factory=list)
    condition_ids: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)   # where it came from
    command: str | None = None
    exit_code: int | None = None
    executed: int | None = None         # tests actually run
    failed: int | None = None
    skipped: int | None = None
    summary: str | None = None          # verbatim summary line, if any
    log_path: str | None = None
    duration_s: float | None = None
    participants: dict[str, str] = field(default_factory=dict)   # repo -> sha, for cross-app
    revision_time: str | None = None    # commit time of `revision`, for ordering
    recorded_at: str = field(default_factory=now_iso)
    detail: dict[str, Any] = field(default_factory=dict)
    record_id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind {self.kind!r}")
        if self.verdict not in VERDICTS:
            raise ValueError(f"unknown verdict {self.verdict!r}")
        if self.platform not in PLATFORMS:
            raise ValueError(f"unknown platform {self.platform!r}")
        if not self.record_id:
            self.record_id = self.identity()

    def identity(self) -> str:
        """Content identity. Store decides whether a repeated result is a new observation."""
        key = {
            "kind": self.kind,
            "repo": self.repo,
            "revision": self.revision,
            "platform": self.platform,
            "condition_ids": sorted(self.condition_ids),
            "source": self.source,
            "command": self.command,
            "exit_code": self.exit_code,
            "executed": self.executed,
            "failed": self.failed,
            "skipped": self.skipped,
            "verdict": self.verdict,
            "participants": dict(sorted(self.participants.items())),
        }
        # For observations whose content is the summary itself (a change from X to Y, a
        # failure reason), the summary is part of what makes two records different.
        if self.kind in ("change", "collection_failure", "revision"):
            key["summary"] = self.summary
            key["old"] = self.detail.get("old")
        elif self.kind == "release_artifact":
            key["summary"] = self.summary
            key["detail"] = self.detail
        blob = json.dumps(key, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()[:24]

    def series_identity(self) -> str:
        """Identity of the check stream, excluding the outcome that can change over time."""
        stable_source = {
            name: self.source[name]
            for name in ("type", "check")
            if name in self.source
        }
        key = {
            "kind": self.kind,
            "repo": self.repo,
            "revision": self.revision,
            "platform": self.platform,
            "condition_ids": sorted(self.condition_ids),
            "source": stable_source,
            "participants": dict(sorted(self.participants.items())),
        }
        blob = json.dumps(key, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()[:24]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Record":
        return cls(**d)


class Store:
    """Append-only JSONL with an in-memory index. Writes are atomic per record."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ids: set[str] = set()
        self._records: list[Record] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = Record.from_dict(json.loads(line))
                if rec.record_id not in self._ids:
                    self._ids.add(rec.record_id)
                    self._records.append(rec)

    def __len__(self) -> int:
        return len(self._records)

    def all(self) -> list[Record]:
        return list(self._records)

    def add(self, rec: Record) -> bool:
        """Store a record, collapsing only consecutive identical observations."""
        if rec.record_id in self._ids:
            series = rec.series_identity()
            previous = next(
                (item for item in reversed(self._records) if item.series_identity() == series),
                None,
            )
            if previous is not None and previous.identity() == rec.identity():
                return False
            counter = len(self._records)
            while rec.record_id in self._ids:
                seed = f"{rec.identity()}:{rec.recorded_at}:{counter}".encode()
                rec.record_id = hashlib.sha256(seed).hexdigest()[:24]
                counter += 1
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(rec.to_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._ids.add(rec.record_id)
        self._records.append(rec)
        return True

    def add_all(self, recs: Iterable[Record]) -> int:
        return sum(1 for r in recs if self.add(r))

    def by_kind(self, kind: str) -> Iterator[Record]:
        return (r for r in self._records if r.kind == kind)

    def latest_revision(self, repo: str) -> Record | None:
        """The newest observed default-branch head for a repo, by commit time."""
        cands = [r for r in self._records if r.kind == "revision" and r.repo == repo]
        if not cands:
            return None
        return max(cands, key=lambda r: (instant_key(r.revision_time), instant_key(r.recorded_at)))

    def evidence_for(self, condition_id: str) -> list[Record]:
        return [
            r for r in self._records
            if condition_id in r.condition_ids and r.kind not in ("revision", "change")
        ]


def atomic_write(path: Path, text: str) -> None:
    """Write a whole file so readers never see a half-written report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
