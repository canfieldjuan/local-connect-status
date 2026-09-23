#!/usr/bin/env python3
"""Record a human demonstration or a release observation as evidence.

A person ran something the collector cannot (an installed-app removal test that needs sudo,
a live calendar write against a real tenant) and observed the result. This records that
observation with the artifact it left and the exact revisions of every participant. It is
the only way installed_demo evidence enters the store, and it requires --observed-by.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lcstatus import catalogue as catmod  # noqa: E402
from lcstatus.evidence import (  # noqa: E402
    Record, Store, check_fingerprint, condition_fingerprint_map,
)
from lcstatus.sources import Mirrors  # noqa: E402


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", required=True)
    ap.add_argument("--participant", action="append", default=[], metavar="repo=sha")
    ap.add_argument("--verdict", required=True, choices=["pass", "fail", "partial"])
    ap.add_argument("--platform", required=True, choices=["linux", "windows", "macos", "n/a"])
    ap.add_argument("--artifact", required=True, help="where the transcript/record lives")
    ap.add_argument("--summary", required=True)
    ap.add_argument("--observed-at", required=True)
    ap.add_argument("--observed-by", required=True)
    a = ap.parse_args()
    try:
        observed_at = normalize_observed_at(a.observed_at)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    cat = catmod.load(ROOT / "catalogue.json")
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
    mirrors = Mirrors(ROOT / ".cache" / "mirrors", cat["repos"])
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
    store = Store(ROOT / "data" / "records.jsonl")
    print("stored" if store.add(rec) else "already recorded (identical)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
