#!/usr/bin/env python3
"""Record a human demonstration or a release observation as evidence.

A person ran something the collector cannot (an installed-app removal test that needs sudo,
a live calendar write against a real tenant) and observed the result. This records that
observation with the artifact it left and the exact revisions of every participant. It is
the only way installed_demo evidence enters the store, and it requires --observed-by.
It waits for a running collection to finish before it reads or writes anything.

    python scripts/record_observation.py --check manual.ip_removal_test \\
        --participant invoice-processor=5b438baa... --participant eom-email-watcher=c3b5cf64... \\
        --verdict pass --platform linux --artifact "docs/contracts/SLICE-7.md Appendix D" \\
        --summary "19 of 19 checks; ledger left in place" --observed-at 2026-09-08T10:47:51-05:00 \\
        --observed-by "Juan Canfield"
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lcstatus import catalogue as catmod  # noqa: E402
from lcstatus.evidence import (  # noqa: E402
    Record, Store, check_fingerprint, collection_lock, condition_fingerprint_map,
)
from lcstatus.sources import Mirrors  # noqa: E402
from lcstatus.sources import Failure  # noqa: E402
from lcstatus.promotion import installed_programs, package_identity, read_regular  # noqa: E402
from lcstatus import install_gap  # noqa: E402
from lcstatus.rules import validate_promotion_member  # noqa: E402

from lcstatus.paths import Paths, outside_code

DATA = Paths.configured().data
DEFAULT_DATA = DATA
CATALOGUE = ROOT / "catalogue.json"
MIRRORS = Paths.configured().cache / "mirrors"
DEFAULT_MIRRORS = MIRRORS


FULL_SHA = re.compile(r"[0-9a-f]{40}")
MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_COMMIT_CLOCK_SKEW = timedelta(minutes=5)


def parse_aware_timestamp(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def normalize_observed_at(value: str, *, now: datetime | None = None) -> str:
    """Validate an operator timestamp and normalize it for chronological string ordering."""
    normalized = parse_aware_timestamp(value, label="--observed-at")
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if normalized > reference + MAX_FUTURE_SKEW:
        raise ValueError("--observed-at cannot be more than 5 minutes in the future")
    return normalized.isoformat(timespec="seconds")


def validate_observation_after_revisions(observed_at: str, revision_times: dict[str, str]) -> None:
    """Reject evidence that claims a revision before that revision existed."""
    observed = parse_aware_timestamp(observed_at, label="--observed-at")
    for repo, committed_at in revision_times.items():
        committed = parse_aware_timestamp(committed_at, label=f"{repo} commit time")
        if observed + MAX_COMMIT_CLOCK_SKEW < committed:
            raise ValueError(
                f"--observed-at predates the {repo} participant revision by more than 5 minutes"
            )


def parse_participants(values: list[str], expected_repos: list[str]) -> dict[str, str]:
    """Accept exactly one full Git SHA for every catalogue-declared participant."""
    expected = set(expected_repos)
    parts: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--participant must use repo=sha")
        repo, sha = value.split("=", 1)
        if repo not in expected:
            raise ValueError(f"unexpected --participant repository: {repo}")
        if repo in parts:
            raise ValueError(f"duplicate --participant repository: {repo}")
        if FULL_SHA.fullmatch(sha) is None:
            raise ValueError(f"--participant {repo} must use a full lowercase 40-character Git SHA")
        parts[repo] = sha
    missing = [repo for repo in expected_repos if repo not in parts]
    if missing:
        raise ValueError(f"missing --participant {missing[0]}=<sha>")
    return parts


def named_paths(values: list[str], apps: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        name, sep, path = value.partition("=")
        if not sep or name not in apps or name in result or not path:
            raise ValueError("package/fallback arguments must be distinct app=path values")
        result[name] = Path(path).expanduser().absolute()
    return result


def record_promotion(args, cat: dict, observed_at: str, data: Path, mirrors_root: Path) -> int:
    """Prepare every member under the collection lock; write one record after all refusals."""
    try:
        apps = args.app
        if not apps or len(set(apps)) != len(apps) or any(
                app not in cat["apps"] or "promotion" not in cat["apps"][app] for app in apps):
            raise ValueError("promotion requires distinct catalogue --app values")
        if args.verdict != "pass" or args.platform != "linux" or not args.observed_by.strip():
            raise ValueError("promotion requires pass, linux and a human --observed-by")
        packages = named_paths(args.package, apps)
        fallbacks = named_paths(args.rollback_package, apps)
        gap_paths = named_paths(getattr(args, "install_gap", []), apps)
        reuse = {}
        for value in getattr(args, "gap_record", []):
            app, sep, record_id = value.partition("=")
            if not sep or app not in apps or app in reuse or app in gap_paths:
                raise ValueError("gap inputs must be distinct app=path or app=record-id values")
            reuse[app] = install_gap.hexadecimal(record_id, 24)
        if set(packages) != set(apps):
            raise ValueError("promotion requires one --package app=path for every app")
        expected_repos = [cat["apps"][app]["repo"] for app in apps]
        parts = parse_participants(args.participant, expected_repos)
        mirrors = Mirrors(mirrors_root, cat["repos"])
        store = Store(data / "records.jsonl")
        records = store.all()
        by_id = {r.record_id: r for r in records}
        def latest(app):
            return install_gap.latest_promotion(app, args.platform, records)
        reused = {}
        completed = set()
        for app, record_id in reuse.items():
            if record_id not in by_id:
                raise ValueError("retained gap record is missing")
            reused[app] = by_id[record_id]
            install_gap.decode_record(reused[app])
            previous = latest(app)
            if previous and previous.detail["members"][app]["fallback"].get("install_gap_record_id") == record_id:
                completed.add(previous.record_id)
        if len(completed) > 1:
            raise ValueError("retry spans different completed promotions")
        completion = by_id[next(iter(completed))] if completed else None
        if completion and (set(completion.detail["members"]) != set(apps) or completion.participants != parts):
            raise ValueError("retry must cover the exact completed promotion set")
        members = {}
        revision_times = {}
        gap_records = {}
        new_gaps = []
        for app in apps:
            row = cat["apps"][app]
            repo, config = row["repo"], row["promotion"]
            revision = parts[repo]
            committed = mirrors.commit_time(repo, revision)
            if committed is None:
                raise ValueError("promotion revision missing from owned mirror")
            revision_times[repo] = committed
            main = mirrors.main_first_parent(repo)
            if isinstance(main, Failure):
                raise ValueError(main.why)
            current_previous = latest(app)
            previous = current_previous
            gap = None
            if app in gap_paths or app in reused:
                gap = (install_gap.decode_record(reused[app]) if app in reused else
                       install_gap.Gap.decode(install_gap.json_bytes(read_regular(gap_paths[app]))))
                if (gap.app, gap.repo, gap.platform, gap.candidate_revision) != (app, repo, args.platform, revision):
                    raise ValueError("gap does not bind this promotion member")
                previous = by_id.get(gap.last_recorded_promotion_id)
                if (previous is None or previous.kind != "promotion" or previous.verdict != "pass" or
                        previous.platform != args.platform or app not in previous.detail.get("members", {}) or
                        previous.detail["members"][app].get("repo") != repo or
                        previous.participants.get(repo) != previous.detail["members"][app].get("revision") or
                        not previous.source.get("observed_by", "").strip() or
                        not install_gap.valid_link(previous, app, records)):
                    raise ValueError("gap predecessor is not a valid retained promotion")
                expected_latest = completion if completion else previous
                if current_previous is None or current_previous.record_id != expected_latest.record_id:
                    raise ValueError("gap predecessor is stale")
                for event in gap.installs:
                    committed_event = mirrors.commit_time(repo, event.revision)
                    if committed_event is None:
                        raise ValueError("historical install revision missing from owned mirror")
                    validate_observation_after_revisions(event.installed_at, {repo: committed_event})
            elif completion:
                if current_previous is None or current_previous.record_id != completion.record_id:
                    raise ValueError("completed promotion is no longer latest")
                previous_id = completion.detail["members"][app]["fallback"]["previous_record"]
                previous = by_id.get(previous_id) if previous_id else None
            if previous is None:
                paths = mirrors.files_at(repo, revision)
            else:
                old = previous.participants[repo]
                if old not in main or revision not in main or main.index(old) < main.index(revision):
                    raise ValueError("promotion cannot precede the previous promotion on main")
                paths = mirrors.touched_files(repo, old, revision)
            if isinstance(paths, Failure):
                raise ValueError(paths.why)
            installed = installed_programs(config["programs"])
            package_sha, packaged_hashes = package_identity(packages[app], config["programs"])
            validate_promotion_member(app, row, revision, main, packaged_hashes, installed,
                                      args.gate_record, paths, records, cat, parts, args.platform)
            if gap is not None:
                if app in fallbacks and str(fallbacks[app]) != gap.previous.package:
                    raise ValueError("rollback argument overrides the admitted gap fallback")
                gap_record = reused.get(app)
                if gap_record is None:
                    gap_record = install_gap.make_record(gap, observer=args.observed_by, observed_at=observed_at,
                        artifact=Path(args.artifact), gate_ids=args.gate_record, revision_time=committed)
                    if gap_record.record_id in by_id:
                        raise ValueError("use --gap-record to retry a retained gap")
                    new_gaps.append(gap_record)
                if (gap_record.source["gate_record_ids"] != sorted(set(args.gate_record)) or
                        gap_record.source["observed_by"] != args.observed_by or
                        gap_record.source["artifact"] != args.artifact or
                        parse_aware_timestamp(gap_record.recorded_at, label="gap time") >
                        parse_aware_timestamp(observed_at, label="promotion time")):
                    raise ValueError("retry changed gap observation or gate inputs")
                gap_records[app] = gap_record
                fallback_info = install_gap.fallback(gap, gap_record)
            else:
                fallback_path = fallbacks.get(app, packages[app] if previous is None else None)
                if fallback_path is None:
                    raise ValueError("later promotions require the real previous install's fallback package")
                fallback_sha, fallback_hashes = package_identity(fallback_path, config["programs"])
                if previous is None:
                    if fallback_sha != package_sha:
                        raise ValueError("starting-point fallback must be the currently installed package")
                else:
                    old_member = previous.detail["members"][app]
                    if fallback_sha != old_member["package_sha256"] or fallback_hashes != old_member["program_hashes"]:
                        raise ValueError("fallback does not match the real previous install")
                fallback_info = {"path": str(fallback_path), "sha256": fallback_sha,
                                 "starting_point": previous is None,
                                 "previous_record": previous.record_id if previous else None}
            members[app] = {"repo": repo, "revision": revision,
                            "config": check_fingerprint(config), "package": str(packages[app]),
                            "package_sha256": package_sha,
                            "program_hashes": installed["hashes"],
                            "fallback": fallback_info}
        validate_observation_after_revisions(observed_at, revision_times)
        by_id = {r.record_id: r for r in records}
        for record_id in args.gate_record:
            if parse_aware_timestamp(by_id[record_id].recorded_at, label="gate time") > parse_aware_timestamp(observed_at, label="promotion time"):
                raise ValueError("promotion observation predates a gate record")
        primary = expected_repos[0]
        record = Record(kind="promotion", repo=primary, revision=parts[primary],
                        revision_time=revision_times[primary], participants=parts, verdict="pass",
                        platform=args.platform, recorded_at=observed_at, summary=args.summary,
                        source={"type": "promotion", "observed_by": args.observed_by,
                                "observed_at": observed_at, "artifact": args.artifact},
                        detail={"members": members, "gate_record_ids": sorted(set(args.gate_record)) if gap_records else args.gate_record})
        for app, member in members.items():
            config = cat["apps"][app]["promotion"]
            if installed_programs(config["programs"])["hashes"] != member["program_hashes"]:
                raise ValueError("installed programs changed before publication")
            for path, digest in [(member["package"], member["package_sha256"]),
                                 (member["fallback"]["path"], member["fallback"]["sha256"])]:
                if hashlib.sha256(read_regular(Path(path))).hexdigest() != digest:
                    raise ValueError("package changed before publication")
        for gap_record in gap_records.values():
            gap = install_gap.decode_record(gap_record)
            gap.verify_history(by_id[gap.last_recorded_promotion_id], gap_record.recorded_at)
            if hashlib.sha256(read_regular(Path(gap_record.source["artifact"]))).hexdigest() != gap_record.source["artifact_sha256"]:
                raise ValueError("human-reviewed artifact changed")
        if completion:
            if record.identity() != completion.identity():
                raise ValueError("retry changed completed promotion inputs")
            print("already recorded " + completion.record_id)
            return 0
        if any(not install_gap.valid_link(record, app, records + new_gaps + [record]) for app in gap_records):
            raise ValueError("gap promotion linkage failed")
        for gap_record in new_gaps:
            store.add(gap_record)
            print("retained gap " + gap_record.record_id)
        print("stored" if store.add(record) else "already recorded (identical)")
        if gap_records:
            retained = {r.record_id: r for r in Store(data / "records.jsonl").all()}
            if record.record_id not in retained or any(r.record_id not in retained for r in gap_records.values()):
                raise OSError("gap/promotion identities were not retained")
        return 0
    except (ValueError, OSError, tarfile.TarError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["installed_demo", "promotion"], default="installed_demo")
    ap.add_argument("--check")
    ap.add_argument("--app", action="append", default=[])
    ap.add_argument("--package", action="append", default=[], metavar="app=path")
    ap.add_argument("--rollback-package", action="append", default=[], metavar="app=path")
    ap.add_argument("--gate-record", action="append", default=[])
    ap.add_argument("--install-gap", action="append", default=[], metavar="app=manifest.json")
    ap.add_argument("--gap-record", action="append", default=[], metavar="app=record-id")
    ap.add_argument("--data", type=Path, default=DATA if DATA != DEFAULT_DATA else None)
    ap.add_argument("--catalogue", type=Path, default=CATALOGUE)
    ap.add_argument("--mirrors", type=Path, default=MIRRORS if MIRRORS != DEFAULT_MIRRORS else None)
    ap.add_argument("--participant", action="append", default=[], metavar="repo=sha")
    ap.add_argument("--verdict", required=True, choices=["pass", "fail", "partial"])
    ap.add_argument("--platform", required=True, choices=["linux", "windows", "macos", "n/a"])
    ap.add_argument("--artifact", required=True, help="where the transcript/record lives")
    ap.add_argument("--summary", required=True)
    ap.add_argument("--observed-at", required=True)
    ap.add_argument("--observed-by", required=True)
    a = ap.parse_args()
    if a.kind == "installed_demo" and not a.check:
        ap.error("--check is required for installed_demo")
    if a.kind != "promotion" and (a.install_gap or a.gap_record):
        ap.error("gap inputs require --kind promotion")
    try:
        observed_at = normalize_observed_at(a.observed_at)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    paths = Paths.configured(data=a.data)
    a.data = paths.data
    a.mirrors = outside_code(a.mirrors) if a.mirrors is not None else paths.cache / "mirrors"

    # One writer at a time (contract 08 B5): wait for any running collection, then read the
    # catalogue, the mirrors and the store as they are now.  The lock is held until this process exits.
    lock = collection_lock(a.data, wait=True)  # noqa: F841
    cat = catmod.load(a.catalogue)
    if a.kind == "promotion":
        return record_promotion(a, cat, observed_at, a.data, a.mirrors)
    chk = cat["checks"].get(a.check)
    if not chk or chk["runner"] != "manual_observation":
        print(f"{a.check} is not a manual_observation check", file=sys.stderr)
        return 2
    expected_repos = catmod.declared_participants(chk)
    try:
        parts = parse_participants(a.participant, expected_repos)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    mirrors = Mirrors(a.mirrors, cat["repos"])
    primary = chk["repo"]
    sha = parts[primary]
    revision_times: dict[str, str] = {}
    for repo, participant_sha in parts.items():
        committed_at = mirrors.commit_time(repo, participant_sha)
        if committed_at is None:
            print(f"--participant {repo} revision is not present in its owned mirror", file=sys.stderr)
            return 2
        revision_times[repo] = committed_at
    try:
        validate_observation_after_revisions(observed_at, revision_times)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    conds = [c["id"] for t in cat["tasks"] for c in t["conditions"] if c["check"] == a.check]
    tasks = [t["id"] for t in cat["tasks"] if any(c["check"] == a.check for c in t["conditions"])]
    rec = Record(kind="installed_demo", repo=primary, revision=sha, revision_time=revision_times[primary],
                 verdict=a.verdict, platform=a.platform, condition_ids=conds, task_ids=tasks,
                 participants=parts, summary=a.summary, recorded_at=observed_at,
                 source={"type": "manual_observation", "check": a.check,
                         "check_fingerprint": check_fingerprint(chk),
                         "condition_fingerprints": condition_fingerprint_map(cat, conds),
                         "artifact": a.artifact,
                         "observed_by": a.observed_by, "observed_at": observed_at})
    store = Store(a.data / "records.jsonl")
    print("stored" if store.add(rec) else "already recorded (identical)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
