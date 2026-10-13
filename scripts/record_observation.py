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
from lcstatus.promotion import installed_programs, package_programs, sha256_file  # noqa: E402
from lcstatus.rules import validate_promotion_member  # noqa: E402

DATA = ROOT / "data"
CATALOGUE = ROOT / "catalogue.json"
MIRRORS = ROOT / ".cache" / "mirrors"


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
        if set(packages) != set(apps):
            raise ValueError("promotion requires one --package app=path for every app")
        expected_repos = [cat["apps"][app]["repo"] for app in apps]
        parts = parse_participants(args.participant, expected_repos)
        mirrors = Mirrors(mirrors_root, cat["repos"])
        store = Store(data / "records.jsonl")
        records = store.all()
        members = {}
        revision_times = {}
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
            prior = [r for r in records if r.kind == "promotion" and r.verdict == "pass"
                     and r.platform == args.platform and app in r.detail.get("members", {})]
            previous = max(enumerate(prior), key=lambda pair: (
                parse_aware_timestamp(pair[1].recorded_at, label="previous promotion time"), pair[0]))[1] if prior else None
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
            packaged_hashes = package_programs(packages[app], config["programs"])
            validate_promotion_member(app, row, revision, main, packaged_hashes, installed,
                                      args.gate_record, paths, records, cat, parts, args.platform)
            fallback = fallbacks.get(app, packages[app] if previous is None else None)
            if fallback is None:
                raise ValueError("later promotions require the real previous install's fallback package")
            fallback_sha = sha256_file(fallback)
            if previous is None:
                if fallback_sha != sha256_file(packages[app]):
                    raise ValueError("starting-point fallback must be the currently installed package")
            else:
                old_member = previous.detail["members"][app]
                if fallback_sha != old_member["package_sha256"] or package_programs(
                        fallback, config["programs"]) != old_member["program_hashes"]:
                    raise ValueError("fallback does not match the real previous install")
            members[app] = {"repo": repo, "revision": revision,
                            "config": check_fingerprint(config), "package": str(packages[app]),
                            "package_sha256": sha256_file(packages[app]),
                            "program_hashes": installed["hashes"],
                            "fallback": {"path": str(fallback), "sha256": fallback_sha,
                                         "starting_point": previous is None,
                                         "previous_record": previous.record_id if previous else None}}
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
                        detail={"members": members, "gate_record_ids": args.gate_record})
        print("stored" if store.add(record) else "already recorded (identical)")
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
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--catalogue", type=Path, default=CATALOGUE)
    ap.add_argument("--mirrors", type=Path, default=MIRRORS)
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
    try:
        observed_at = normalize_observed_at(a.observed_at)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

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
