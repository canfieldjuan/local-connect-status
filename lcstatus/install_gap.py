"""One closed decoder for install-gap evidence, shared by recording and matching.

Only verify_history reads files. Stored-link validation is pure and never reopens receipts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess

from .evidence import Record, instant_key
from .promotion import read_regular, package_programs_bytes


def closed(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("invalid or unknown install-gap fields")
    return value


def text(value):
    if type(value) is not str or not value.strip():
        raise ValueError("install-gap text must be nonblank")
    return value


def hexadecimal(value, size):
    if type(value) is not str or len(value) != size or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("install-gap identity must be a full lowercase digest")
    return value


def aware(value):
    if type(value) is not str or instant_key(value)[0] == 0:
        raise ValueError("install-gap time must be timezone-aware")
    return datetime.fromtimestamp(instant_key(value)[1], timezone.utc).isoformat()


def absolute(value):
    value = text(value)
    if not Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("install-gap evidence paths must be absolute")
    return value


def programs(value):
    if type(value) is not dict or not value:
        raise ValueError("install-gap program identity must be nonempty")
    result = []
    for name, digest in value.items():
        absolute(name)
        if str(PurePosixPath(name)) != name:
            raise ValueError("noncanonical installed program path")
        result.append((name, hexadecimal(digest, 64)))
    return tuple(sorted(result))


def json_bytes(content):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate evidence JSON key")
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError("invalid JSON constant")
    return json.loads(content, object_pairs_hook=pairs, parse_constant=invalid_constant)


def latest_promotion(app, platform, records):
    prior = [(i, r) for i, r in enumerate(records) if r.kind == "promotion" and r.verdict == "pass"
             and r.platform == platform and app in r.detail.get("members", {})]
    for _, record in prior:
        aware(record.recorded_at)
    return max(prior, key=lambda pair: (instant_key(pair[1].recorded_at)[1], pair[0]))[1] if prior else None


@dataclass(frozen=True)
class Receipt:
    format: str
    path: str
    sha256: str

    @classmethod
    def decode(cls, value):
        closed(value, {"format", "path", "sha256"})
        if value["format"] not in ("installed-v1", "ew-package", "ew-acceptance"):
            raise ValueError("unsupported installed receipt layout")
        return cls(value["format"], absolute(value["path"]), hexadecimal(value["sha256"], 64))

    def read(self):
        content = read_regular(Path(self.path))
        if hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("retained receipt hash mismatch")
        result = json_bytes(content)
        if type(result) is not dict:
            raise ValueError("installed receipt must be an object")
        return result


@dataclass(frozen=True)
class Install:
    revision: str
    installed_at: str
    package_sha256: str
    program_hashes: tuple[tuple[str, str], ...]
    receipts: tuple[Receipt, ...]
    observation_ids: tuple[str, ...]
    package: str | None

    @classmethod
    def decode(cls, value):
        closed(value, {"revision", "installed_at", "package_sha256", "program_hashes", "receipts",
                       "installed_observation_record_ids"}, {"package"})
        refs = value["receipts"]
        ids = value["installed_observation_record_ids"]
        if type(refs) is not list or type(ids) is not list:
            raise ValueError("installed evidence references must be arrays")
        decoded = tuple(Receipt.decode(r) for r in refs)
        observed = tuple(hexadecimal(v, 24) for v in ids)
        if len(observed) != len(set(observed)) or not observed and not any(r.format == "installed-v1" for r in decoded):
            raise ValueError("event requires collector IDs or hash-bound installed receipt")
        if not decoded and "package" not in value:
            raise ValueError("event requires source-bound package evidence")
        return cls(hexadecimal(value["revision"], 40), aware(value["installed_at"]),
                   hexadecimal(value["package_sha256"], 64), programs(value["program_hashes"]),
                   decoded, observed, absolute(value["package"]) if "package" in value else None)

    def wire(self):
        result = {"revision": self.revision, "installed_at": self.installed_at,
                  "package_sha256": self.package_sha256, "program_hashes": dict(self.program_hashes),
                  "installed_observation_record_ids": list(self.observation_ids),
                  "receipts": [dict(format=r.format, path=r.path, sha256=r.sha256) for r in self.receipts]}
        if self.package is not None:
            result["package"] = self.package
        return result


@dataclass(frozen=True)
class Gap:
    app: str
    repo: str
    platform: str
    candidate_revision: str
    last_recorded_promotion_id: str
    installs: tuple[Install, ...]

    @classmethod
    def decode(cls, value):
        closed(value, {"version", "app", "repo", "platform", "candidate_revision",
                       "last_recorded_promotion_id", "unrecorded_installs", "history_attested_complete"})
        if type(value["version"]) is not int or value["version"] != 2 or value["history_attested_complete"] is not True:
            raise ValueError("unsupported gap version or missing human completeness attestation")
        if value["platform"] != "linux":
            raise ValueError("install gaps support linux only")
        raw = value["unrecorded_installs"]
        if type(raw) is not list or not raw:
            raise ValueError("unrecorded installs must be nonempty")
        events = tuple(Install.decode(v) for v in raw)
        if events[-1].package is None:
            raise ValueError("real previous install requires its retained package")
        times = [instant_key(e.installed_at)[1] for e in events]
        refs = [(r.path, r.sha256) for e in events for r in e.receipts]
        ids = [v for e in events for v in e.observation_ids]
        if (any(a >= b for a, b in zip(times, times[1:])) or len(refs) != len(set(refs)) or
                len(ids) != len(set(ids))):
            raise ValueError("duplicate or unordered installed evidence")
        return cls(text(value["app"]), text(value["repo"]), "linux",
                   hexadecimal(value["candidate_revision"], 40),
                   hexadecimal(value["last_recorded_promotion_id"], 24), events)

    def wire(self):
        return {"version": 2, "app": self.app, "repo": self.repo, "platform": self.platform,
                "candidate_revision": self.candidate_revision,
                "last_recorded_promotion_id": self.last_recorded_promotion_id,
                "unrecorded_installs": [e.wire() for e in self.installs], "history_attested_complete": True}

    @property
    def previous(self):
        return self.installs[-1]

    def verify_window(self, predecessor, observed_at):
        start = instant_key(predecessor.recorded_at)[1]
        end = instant_key(aware(observed_at))[1]
        if not start < instant_key(self.installs[0].installed_at)[1] < end or instant_key(self.previous.installed_at)[1] >= end:
            raise ValueError("gap history is outside its recorded predecessor/candidate interval")

    def validate_observations(self, predecessor, observed_at, records, candidate_programs, *, gap_record_id=None):
        """Pure by-ID proof and ordered completeness, shared by writes and retained matching."""
        self.verify_window(predecessor, observed_at)
        start = instant_key(aware(predecessor.recorded_at))[1]
        end = instant_key(aware(observed_at))[1]
        candidate = programs(candidate_programs)
        previous = programs(predecessor.detail["members"][self.app]["program_hashes"])
        indexed = {r.record_id: (i, r) for i, r in enumerate(records)}
        if len(indexed) != len(records):
            raise ValueError("ambiguous retained observation IDs")
        reference_limit = indexed[gap_record_id][0] if gap_record_id in indexed else len(records)

        def identity(row):
            if (type(row.source) is not dict or type(row.detail) is not dict or
                    row.kind != "installed_observation" or row.repo != self.repo or row.platform != self.platform or
                    row.source.get("type") != "installed_program_hashes" or row.source.get("app") != self.app or
                    row.detail.get("app") != self.app):
                raise ValueError("installed observation attribution mismatch")
            installed = closed(row.detail.get("installed"), {"hashes", "missing", "errors"})
            if type(installed["missing"]) is not list or type(installed["errors"]) is not dict:
                raise ValueError("invalid installed observation result")
            if installed["missing"] or installed["errors"]:
                return None
            return programs(installed["hashes"])

        samples = []
        for i, row in enumerate(records):
            if (row.kind == "installed_observation" and row.repo == self.repo and row.platform == self.platform and
                    type(row.source) is dict and
                    row.source.get("type") == "installed_program_hashes" and row.source.get("app") == self.app):
                at = instant_key(aware(row.recorded_at))[1]
                if start < at < end:
                    value = identity(row)
                    if value is not None:
                        samples.append((at, i, value))
        samples.sort()
        if any(a[0] == b[0] and a[2] != b[2] for a, b in zip(samples, samples[1:])):
            raise ValueError("conflicting installed identities at one instant")
        boundary = end
        for at, _, value in reversed(samples):
            if value != candidate:
                break
            boundary = at
        times = [instant_key(e.installed_at)[1] for e in self.installs]
        if times[-1] >= boundary:
            raise ValueError("gap event reaches the terminal candidate boundary")
        for index, event in enumerate(self.installs):
            upper = times[index + 1] if index + 1 < len(times) else boundary
            for record_id in event.observation_ids:
                if record_id not in indexed or indexed[record_id][0] >= reference_limit:
                    raise ValueError("installed observation is missing or was retained after the gap")
                row = indexed[record_id][1]
                at = instant_key(aware(row.recorded_at))[1]
                if not start < at or not times[index] <= at < upper or identity(row) != event.program_hashes:
                    raise ValueError("installed observation hash/time binding mismatch")
        # Time intervals preserve repeated transitions, including an unseen event between equal samples.
        event_index = -1
        for at, _, value in samples:
            if at >= boundary:
                continue
            while event_index + 1 < len(times) and times[event_index + 1] <= at:
                event_index += 1
            if event_index < 0:
                if value != previous:
                    raise ValueError("observed installed identity omitted from gap")
            elif value != self.installs[event_index].program_hashes:
                raise ValueError("observed installed identity omitted or reordered")
            elif not self.installs[event_index].observation_ids:
                raise ValueError("collector-witnessed event requires retained observation IDs")

    def verify_history(self, predecessor, observed_at, records, candidate_programs, *, gap_record_id=None):
        self.verify_window(predecessor, observed_at)
        for event in self.installs:
            installed = package_seen = False
            for ref in event.receipts:
                receipt = ref.read()
                if ref.format == "installed-v1":
                    closed(receipt, {"version", "app", "repo", "platform", "installed", "revision",
                                     "installed_at", "package_sha256", "program_hashes"})
                    if (type(receipt["version"]) is not int or receipt["version"] != 1 or
                            receipt["installed"] is not True or receipt["app"] != self.app or
                            receipt["repo"] != self.repo or receipt["platform"] != self.platform or
                            receipt["revision"] != event.revision or aware(receipt["installed_at"]) != event.installed_at or
                            receipt["package_sha256"] != event.package_sha256 or programs(receipt["program_hashes"]) != event.program_hashes):
                        raise ValueError("installed receipt identity mismatch")
                    installed = True
                else:
                    if self.app != "email-watcher" or self.repo != "eom-email-watcher" or receipt.get("source") != event.revision:
                        raise ValueError("Email Watcher receipt source mismatch")
                    if ref.format == "ew-acceptance":
                        if (receipt.get("installed_bytes_match") is not True or receipt.get("linux_installed_acceptance") != "PASS"
                                or aware(receipt.get("verified_at")) != event.installed_at):
                            raise ValueError("receipt does not establish installed-byte equality")
                    else:
                        old_fields = {"package_sha256", "program_hashes"}
                        new_fields = {"deb_sha256", "desktop_sha256", "engine_sha256"}
                        old = old_fields <= receipt.keys()
                        new = new_fields <= receipt.keys()
                        if old == new or old and new_fields & receipt.keys() or new and old_fields & receipt.keys():
                            raise ValueError("unsupported or ambiguous package receipt")
                        if old:
                            if type(receipt["program_hashes"]) is not dict:
                                raise ValueError("invalid package program map")
                            hashes = {}
                            for key, value in receipt["program_hashes"].items():
                                name = "/" + str(PurePosixPath(key)).lstrip("/")
                                if name in hashes:
                                    raise ValueError("duplicate normalized program path")
                                hashes[name] = value
                            digest = receipt["package_sha256"]
                        else:
                            hashes = {"/usr/bin/eom-email-watcher-desktop": receipt["desktop_sha256"],
                                      "/usr/bin/eom-mail-engine": receipt["engine_sha256"]}
                            digest = receipt["deb_sha256"]
                        if digest != event.package_sha256 or programs(hashes) != event.program_hashes:
                            raise ValueError("package receipt identity mismatch")
                        package_seen = True
            if event.package is not None:
                raw = read_regular(Path(event.package))
                if (hashlib.sha256(raw).hexdigest() != event.package_sha256 or
                        package_programs_bytes(raw, list(dict(event.program_hashes))) != dict(event.program_hashes)):
                    raise ValueError("retained fallback/package identity mismatch")
                if not installed and not package_seen:
                    if not raw.startswith(b"!<arch>\n"):
                        raise ValueError("package requires source-bound receipt")
                    result = subprocess.run(["dpkg-deb", "--field", "-", "X-Built-From"], input=raw,
                                            capture_output=True, timeout=120, check=False)
                    if result.returncode or result.stdout.decode().strip() != event.revision:
                        raise ValueError("package source revision mismatch")
                    package_seen = True
            if not installed and not package_seen:
                raise ValueError("event lacks source-bound package evidence")
        self.validate_observations(predecessor, observed_at, records, candidate_programs, gap_record_id=gap_record_id)


SOURCE_KEYS = {"type", "observed_by", "observed_at", "artifact", "artifact_sha256", "install_gap", "gate_record_ids"}


def eligible_for_check(record):
    """Reserved origin discriminator: gap evidence never supplies another check's proof."""
    return record.source.get("type") != "promotion_gap"


def decode_record(record):
    """Enforce the gap's no-attribution invariant by construction, including on readback."""
    closed(record.source, SOURCE_KEYS)
    if (record.kind != "installed_demo" or record.verdict != "pass" or record.condition_ids != [] or
            record.task_ids != [] or record.detail != {} or record.source["type"] != "promotion_gap" or
            record.record_id != record.identity()):
        raise ValueError("invalid gap record envelope or identity")
    gap = Gap.decode(record.source["install_gap"])
    if (record.repo != gap.repo or record.revision != gap.candidate_revision or record.platform != gap.platform or
            record.participants != {gap.repo: gap.candidate_revision} or
            aware(record.recorded_at) != aware(record.source["observed_at"])):
        raise ValueError("gap envelope does not bind its candidate")
    text(record.source["observed_by"])
    absolute(record.source["artifact"])
    hexadecimal(record.source["artifact_sha256"], 64)
    ids = record.source["gate_record_ids"]
    if type(ids) is not list or not ids:
        raise ValueError("gap gate identities must be a nonempty canonical set")
    for value in ids:
        hexadecimal(value, 24)
    if ids != sorted(set(ids)):
        raise ValueError("gap gate identities must be a canonical set")
    return gap


def make_record(gap, *, observer, observed_at, artifact, gate_ids, revision_time):
    source = {"type": "promotion_gap", "observed_by": text(observer), "observed_at": aware(observed_at),
              "artifact": absolute(str(artifact)), "artifact_sha256": hashlib.sha256(read_regular(artifact)).hexdigest(),
              "install_gap": gap.wire(), "gate_record_ids": sorted(set(gate_ids))}
    record = Record(kind="installed_demo", repo=gap.repo, revision=gap.candidate_revision, verdict="pass",
                    platform="linux", participants={gap.repo: gap.candidate_revision}, source=source,
                    recorded_at=source["observed_at"], revision_time=revision_time)
    decode_record(record)
    return record


def fallback(gap, record):
    event = gap.previous
    return {"path": event.package, "sha256": event.package_sha256, "starting_point": False,
            "previous_record": gap.last_recorded_promotion_id, "install_gap_record_id": record.record_id,
            "previous_install_revision": event.revision, "program_hashes": dict(event.program_hashes)}


def valid_link(promotion, app, records):
    """Pure stored-link admission; legacy continuous-history records remain unchanged."""
    try:
        indexed = {r.record_id: (i, r) for i, r in enumerate(records)}
        # Every edge must point backwards. Iteration admits long valid chains without recursion.
        while True:
            member = promotion.detail["members"][app]
            fb = member.get("fallback", {})
            if "install_gap_record_id" not in fb:
                return "previous_install_revision" not in fb
            gap_index, record = indexed[fb["install_gap_record_id"]]
            gap = decode_record(record)
            base_index, base = indexed[gap.last_recorded_promotion_id]
            promotion_index = indexed[promotion.record_id][0]
            latest = latest_promotion(app, gap.platform, records[:promotion_index])
            gap.validate_observations(base, record.recorded_at, records[:promotion_index], member["program_hashes"],
                                      gap_record_id=record.record_id)
            if not (base_index < gap_index < promotion_index and latest is not None and latest.record_id == base.record_id and
                    base.platform == promotion.platform == gap.platform and base.source.get("observed_by", "").strip() and
                    base.detail["members"][app]["repo"] == gap.repo and
                    base.participants[gap.repo] == base.detail["members"][app]["revision"] and
                    promotion.record_id == promotion.identity() and gap.app == app and gap.repo == member["repo"] and
                    gap.candidate_revision == member["revision"] == promotion.participants[gap.repo] and
                    fb == fallback(gap, record) and record.source["gate_record_ids"] == sorted(set(promotion.detail["gate_record_ids"]))):
                return False
            promotion = base
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
