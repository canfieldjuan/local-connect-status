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

import fcntl
import hashlib
import json
import os
import sys
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
    "issue_gate",           # open release-milestone issues observed through GitHub
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


# Free-text keys of a catalogue check.  They explain a check to a reader; they do not change
# what the check runs, where, or against what, so they are not part of evidence identity.
CHECK_PROSE_KEYS = ("note",)


def _fingerprint(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def whole_check_fingerprint(check: dict[str, Any]) -> str:
    """The pre-slice-3 identity: every key of the check, prose included."""
    return _fingerprint(check)


def check_fingerprint(check: dict[str, Any]) -> str:
    """Stable identity for the catalogue configuration that produced evidence.

    Every configuration key participates (runner, repo, platform, args, paths, markers,
    participants, job, workflow, milestone, required_assets, desktop_deps, heavy, ...).  Only
    CHECK_PROSE_KEYS are left out: editing a note must not orphan the evidence it describes.
    """
    return _fingerprint({key: value for key, value in check.items() if key not in CHECK_PROSE_KEYS})


def condition_fingerprint(condition: dict[str, Any]) -> str:
    """Identity for claim fields not already enforced by condition selection."""
    semantics = {
        key: value for key, value in condition.items()
        if key not in ("id", "kind", "check", "platform")
    }
    blob = json.dumps(semantics, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def condition_fingerprint_map(
    catalogue: dict[str, Any], condition_ids: Iterable[str],
) -> dict[str, str]:
    """Fingerprints for the named conditions in a loaded catalogue."""
    wanted = set(condition_ids)
    return {
        condition["id"]: condition_fingerprint(condition)
        for task in catalogue.get("tasks", [])
        for condition in task.get("conditions", [])
        if condition["id"] in wanted
    }


# The first collector release wrote these rows before check fingerprints existed.  Preserve
# that append-only evidence without rewriting it: only this exact byte-for-byte prefix receives
# its check configuration from the catalogue at c482727.  Later fingerprint-free rows and any
# modified legacy prefix fail closed.  A changed current configuration still invalidates the
# row because rules.py compares this historical value with the current check fingerprint.
LEGACY_EVIDENCE_PREFIX_ROWS = 151
LEGACY_EVIDENCE_PREFIX_SHA256 = "1a7f65fe682b277d092d97cf155bb9e1eab73f1afa9884a6b9604f2afbb2da2b"
LEGACY_CHECK_FINGERPRINTS = {
    "ds.ci.rust": "ba862d249e32fcbc64fd24ab",
    "ew.ci.desktop": "2ec5ac88e37a3c4d14f0757e",
    "ew.ci.test": "db60e79047fb756a51d9e02d",
    "ew.ci.windows_lock": "414e04b54e053df156643fd3",
    "ew.ci.windows_package": "d12118d0e23e36ed324db75a",
    "ew.pytest.adapters": "b8c2b5e323a3db727335e516",
    "ew.pytest.automation": "53ad3323e89b847ea05f2d01",
    "ew.pytest.connect_v1": "d8cf91e8b462adf4f824b6ff",
    "ew.pytest.connect_v2": "76fdb4d07bdbfdb4f0d92d9f",
    "ew.pytest.notify": "bf983c0691d198f40bf8bdb5",
    "ew.pytest.queue_retry": "fdb699ce6590f2962dd03199",
    "ew.pytest.scheduling": "a51153f9bf8acfd9a2501af4",
    "ew.pytest.unit": "6b977a25d43c24c44509a87e",
    "ip.pytest.all": "92cb5b7ec9832d2137bbca3d",
    "ip.pytest.entitlement": "137581c260a4001a9291204b",
    "ip.pytest.extraction": "1eca072653a5db9e196760c2",
    "ip.pytest.ledger": "fb54af050265c5c455cae2c7",
    "ip.pytest.ledger_via_connect": "f36793d1236a2ee3deec74c4",
    "ip.pytest.packaging": "33bb78a149dbdd42b9ee2c70",
    "manual.ip_removal_test": "0d1d9d4a0948ea9d35e7ca36",
    "xapp.accept_ew_to_ip": "c9984d8175c41be30015fa1d",
}
LEGACY_CONDITION_FINGERPRINTS = {
    "bill.busy_job_queued_and_resubmitted": "25e96a7d7b9288515c8fcd54",
    "bill.consumer_generic_invoke": "2ece43f6ac0c27e8f52d147b",
    "bill.cross_app_acceptance": "193b1019dd93bdfe06aa6edd",
    "bill.installed_removal_demo": "a8c282c2bc2052f4c274171f",
    "bill.provider_writes_ledger": "275af89eef021c865adea24a",
    "connect.consumer_ci": "aa45222264ee563dea83a307",
    "connect.v1_handoff_durable": "d655704cf8455ff141e0a786",
    "ds.ci_linux": "48bffcf199a3025740ac9868",
    "ew.adapters_read_only": "1fa3fdeb28aa1d5d9176bdd6",
    "ew.automation_gated_and_proposes": "27e77bdac3188ff776de3f43",
    "ew.calendar_write_transactional": "32e138381be613522a7a3f69",
    "ew.ci_linux": "57527eea63636836fa84e030",
    "ew.ci_windows_subset": "2399aa7f47ea7fc856567c4f",
    "ew.notifications_deliver": "a3a4451b7fec6edf65aa51f7",
    "ew.scheduling_extraction_evidence_bound": "e1324d4ca5c156ed26b4fd29",
    "ip.extraction_withholds": "11eec3e69621ae1766c29878",
    "ip.ledger_states_and_dupes": "ae1b9c97e8e86818b27e516f",
    "ip.records_survive_uninstall": "dabfdc1be0c1d842cc648351",
    "ip.suite_green": "5297fe9cd06e64747cde1068",
    "rel.ds_windows": "7a2ac4f145b4091c606e9ab2",
    "rel.ew_linux_desktop_builds": "3fa4c7a0cca55748d3c393b4",
    "rel.ew_windows_installer_builds": "623e32f0bbde454e6a978ef7",
    "rel.ip_packaging_checks": "ad1f65aefde6b8bc45fdb099",
    "rel.ip_removal_demo": "e77c00ed3e37dfc459b6eb63",
    "rel.published": "6652df2bac68a6566231441f",
    "rel.published_ds": "2837ea5a3f5eff1f268ccec7",
    "rel.published_ew": "2591a9cb8fdd5088344e5700",
    "rel.published_ip": "84b64b92c2f103104d9f153b",
}


# Records written before slice 3 carry whole-dict fingerprints.  This table, generated once from
# the catalogue as it stood at that commit (before any note was edited), maps each of those
# values to the semantic fingerprint of the same configuration.  It is frozen data: never
# regenerate it from a later catalogue, or old evidence would re-bind to edited configurations.
CHECK_FINGERPRINT_ALIASES = {
    "0d1d9d4a0948ea9d35e7ca36": "b1b68b4843506fce8c382115",
    "137581c260a4001a9291204b": "137581c260a4001a9291204b",
    "1a0851093ac33e8909a6169c": "c8936dae47fb4431bf00364d",
    "1eca072653a5db9e196760c2": "1eca072653a5db9e196760c2",
    "27756a5feb3d06f6171c9eac": "89876287a67d1ad891e095a2",
    "2ec5ac88e37a3c4d14f0757e": "2ec5ac88e37a3c4d14f0757e",
    "33bb78a149dbdd42b9ee2c70": "33bb78a149dbdd42b9ee2c70",
    "3b25686fe5eb2b5591bb8cb7": "56ffee3d6e394d94868c23bb",
    "3bc6cdd95fae94bb9f98f3c5": "e6c473d97dacb4180b126cc3",
    "414e04b54e053df156643fd3": "414e04b54e053df156643fd3",
    "50024b9286837c3221f5f1dc": "1c0d3b0474d190c8d1f0d3bc",
    "53ad3323e89b847ea05f2d01": "53ad3323e89b847ea05f2d01",
    "56d5119ade2e46ee16aa3418": "56d5119ade2e46ee16aa3418",
    "658ede0d7a0e6a834fef5e2b": "200ee4479003d0b389db13b1",
    "6b977a25d43c24c44509a87e": "541614cfb88bb1da194bf3f3",
    "6c2b84abb387abf7a61d7187": "bf081e8cac089245b856fde0",
    "76fdb4d07bdbfdb4f0d92d9f": "76fdb4d07bdbfdb4f0d92d9f",
    "7d47203ad1e0d1688cf771df": "2f6fe0e80bffc13c6c880ead",
    "822aa20e9cb398ccc79c170d": "ddcc0262449c2de779fd0bb7",
    "8c99c5d3e8cf1ab99f465dbd": "786300b91a795f9dba15abf9",
    "8da5e0f0a285b06967108fcb": "594b0fd6464a1cf12433cb39",
    "900f885f739ccd8706c2e055": "ba7c70b13c7d1f074b9e94b6",
    "92cb5b7ec9832d2137bbca3d": "73778373957ae4813b2001d0",
    "9d49967f5c9de4033b242500": "3faf59294609179735437181",
    "a01b0d2da85c49c8d6efa62e": "857444940db52591e4df8bf9",
    "a464c2b9ca53e451f5f9788a": "884ccadc616f59f20c3239c3",
    "a51153f9bf8acfd9a2501af4": "a51153f9bf8acfd9a2501af4",
    "a9d59cf99a9a0cdaff558fd5": "bf081e8cac089245b856fde0",
    "adccaebb3ee7c6347a8096b7": "939a3c2c2eeafaf6089c605c",
    "b8c2b5e323a3db727335e516": "b8c2b5e323a3db727335e516",
    "ba862d249e32fcbc64fd24ab": "b6551f3a449d33e20917f772",
    "bf983c0691d198f40bf8bdb5": "bf983c0691d198f40bf8bdb5",
    "ca1e4b25405fff9b8d5b246a": "b6a631462b1402fdab610d9f",
    "d12118d0e23e36ed324db75a": "d1a0aced96c8df8677218dcb",
    "d64e6aa995e224789f23f2fe": "830a9866f9f4054b9b3dc572",
    "d8cf91e8b462adf4f824b6ff": "d8cf91e8b462adf4f824b6ff",
    "db09bb11e064c5d91d587952": "4867d41ae88485e94d6b50fe",
    "db60e79047fb756a51d9e02d": "db60e79047fb756a51d9e02d",
    "ddfd39174dc90c040a92f150": "3725fb856de0092eceaa8015",
    "e1dc2a223c7a1005b9ba95b1": "c80dff3996cd73d0630a66a1",
    "e279743963e8a9c91ab52403": "9b90c42d31bf65a1d11b25e7",
    "f36793d1236a2ee3deec74c4": "f36793d1236a2ee3deec74c4",
    "fb54af050265c5c455cae2c7": "fb54af050265c5c455cae2c7",
    "fdb699ce6590f2962dd03199": "fdb699ce6590f2962dd03199",
}


def resolve_check_fingerprint(value: Any) -> Any:
    """Map a stored whole-dict fingerprint to its semantic twin; anything else is itself.

    Total: None and non-string values pass through unchanged, so one malformed row can never
    abort status derivation.  Such a value is never equal to a current fingerprint.
    """
    if not isinstance(value, str):
        return value
    return CHECK_FINGERPRINT_ALIASES.get(value, value)


def record_check_fingerprint(record: Any) -> str | None:
    """Return an explicit fingerprint or an authenticated in-memory legacy migration."""
    explicit = record.source.get("check_fingerprint")
    if explicit is not None:
        return explicit
    return getattr(record, "_legacy_check_fingerprint", None)


def record_condition_fingerprints(record: Any) -> dict[str, str]:
    """Return persisted condition identities or authenticated in-memory legacy identities."""
    explicit = record.source.get("condition_fingerprints")
    if isinstance(explicit, dict):
        return explicit
    return getattr(record, "_legacy_condition_fingerprints", {})


def record_condition_fingerprint(record: Any, condition_id: str) -> str | None:
    return record_condition_fingerprints(record).get(condition_id)


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
            if self.kind == "change":
                key["commits_complete"] = self.detail.get("commits_complete", True)
        elif self.kind in ("issue_gate", "release_artifact"):
            key["summary"] = self.summary
            key["detail"] = self.detail
        blob = json.dumps(key, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()[:24]

    def observation_identity(self) -> str:
        """identity(), with the check fingerprint resolved through the alias table.

        Used only to decide whether a delivery repeats the stored observation before it; a
        fingerprint spelling change is not a new result.  record_id keeps using identity().
        """
        return self.__class__(**{**asdict(self), "source": self._resolved_source()}).identity()

    def _resolved_source(self) -> dict[str, Any]:
        source = dict(self.source)
        if "check_fingerprint" in source:
            source["check_fingerprint"] = resolve_check_fingerprint(source["check_fingerprint"])
        return source

    def series_identity(self) -> str:
        """Identity of the check stream, excluding the outcome that can change over time."""
        # collector_code and repo_config are inputs to a local run (contract 07 B1 rev 3); rows
        # written before they existed carry neither, so their identity is unchanged.
        stable_source = {
            name: self.source[name]
            for name in ("type", "check", "collector_code", "repo_config")
            if name in self.source
        }
        fingerprint = resolve_check_fingerprint(record_check_fingerprint(self))
        if fingerprint is not None:
            stable_source["check_fingerprint"] = fingerprint
        condition_fingerprints = record_condition_fingerprints(self)
        if condition_fingerprints:
            stable_source["condition_fingerprints"] = condition_fingerprints
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


def validate_record_instants(record: Any) -> None:
    """Refuse to write a row whose instants could not be ordered truthfully.

    instant_key orders a naive or unparseable value before every real instant.  That fallback
    exists so an older store still loads; this collector must never produce a row that needs it.
    """
    if instant_key(record.recorded_at)[0] == 0:
        raise ValueError(f"recorded_at is not an aware ISO-8601 instant: {record.recorded_at!r}")
    if record.revision_time is not None and instant_key(record.revision_time)[0] == 0:
        raise ValueError(f"revision_time is not an aware ISO-8601 instant: {record.revision_time!r}")


class Store:
    """Append-only JSONL with an in-memory index. Writes are atomic per record."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ids: set[str] = set()
        self._records: list[Record] = []
        if self.path.exists():
            raw_lines = self.path.read_bytes().splitlines(keepends=True)
            legacy_prefix = raw_lines[:LEGACY_EVIDENCE_PREFIX_ROWS]
            migrate_legacy = (
                len(legacy_prefix) == LEGACY_EVIDENCE_PREFIX_ROWS
                and hashlib.sha256(b"".join(legacy_prefix)).hexdigest() == LEGACY_EVIDENCE_PREFIX_SHA256
            )
            for position, line in enumerate(raw_lines):
                if not line.strip():
                    continue
                rec = Record.from_dict(json.loads(line))
                if (
                    migrate_legacy
                    and position < LEGACY_EVIDENCE_PREFIX_ROWS
                    and "check_fingerprint" not in rec.source
                ):
                    historical = LEGACY_CHECK_FINGERPRINTS.get(rec.source.get("check"))
                    if historical is not None:
                        rec._legacy_check_fingerprint = historical
                    rec._legacy_condition_fingerprints = {
                        condition_id: LEGACY_CONDITION_FINGERPRINTS[condition_id]
                        for condition_id in rec.condition_ids
                        if condition_id in LEGACY_CONDITION_FINGERPRINTS
                    }
                if rec.record_id not in self._ids:
                    self._ids.add(rec.record_id)
                    self._records.append(rec)

    def __len__(self) -> int:
        return len(self._records)

    def latest_in_series(self, rec: Record) -> Record | None:
        """The newest stored row of the same check stream at the same revision(s), if any.

        One definition, used twice: add() collapses a repeat onto it after a run, and the collector
        asks it before a run whether that exact run was already decided (contract 07).
        """
        series = rec.series_identity()
        return next((item for item in reversed(self._records) if item.series_identity() == series), None)

    def all(self) -> list[Record]:
        return list(self._records)

    def add(self, rec: Record) -> bool:
        """Store a record, collapsing only consecutive identical observations.

        "Identical" is judged with check fingerprints resolved, so a result re-delivered under
        the semantic fingerprint collapses onto the same result stored under the whole-dict one.
        A row whose instants are not timezone-aware is refused (ValueError) and never written.
        """
        validate_record_instants(rec)
        previous = self.latest_in_series(rec)
        if previous is not None and previous.observation_identity() == rec.observation_identity():
            return False
        if rec.record_id in self._ids:
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


LOCK_NAME = ".lock"


def collection_lock(data: Path, *, wait: bool) -> Any:
    """Take the one lock every writer of `data/` holds (contract 08 B5): the collector and the
    observation script acquire it here, so its path and its waiting cannot drift apart.

    Returns the open lock file, which holds the lock until it is closed or the process exits, or
    None when another collection holds it and the caller does not wait.  A waiting caller says so
    on stderr before it blocks, so a run that seems stuck names what it is waiting for.
    """
    data = Path(data)
    data.mkdir(parents=True, exist_ok=True)
    fh = open(data / LOCK_NAME, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if not wait:
            fh.close()
            return None
        print("waiting for the collection lock (another collection is running) ...", file=sys.stderr, flush=True)
        fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


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
