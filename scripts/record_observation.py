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
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lcstatus import catalogue as catmod  # noqa: E402
from lcstatus.evidence import Record, Store  # noqa: E402
from lcstatus.sources import Mirrors  # noqa: E402


def normalize_observed_at(value: str) -> str:
    """Validate an operator timestamp and normalize it for chronological string ordering."""
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--observed-at must be an ISO-8601 timestamp") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("--observed-at must include a timezone offset")
    return observed.astimezone(timezone.utc).isoformat(timespec="seconds")


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
    parts = dict(p.split("=", 1) for p in a.participant)
    for repo in chk.get("participants", [chk["repo"]]):
        if repo not in parts:
            print(f"missing --participant {repo}=<sha>", file=sys.stderr)
            return 2
    mirrors = Mirrors(ROOT / ".cache" / "mirrors", cat["repos"])
    primary = chk["repo"]
    sha = parts[primary]
    if len(sha) < 40:
        print("use full 40-character shas so the record cannot be ambiguous", file=sys.stderr)
        return 2
    conds = [c["id"] for t in cat["tasks"] for c in t["conditions"] if c["check"] == a.check]
    tasks = [t["id"] for t in cat["tasks"] if any(c["check"] == a.check for c in t["conditions"])]
    rec = Record(kind="installed_demo", repo=primary, revision=sha, revision_time=mirrors.commit_time(primary, sha),
                 verdict=a.verdict, platform=a.platform, condition_ids=conds, task_ids=tasks,
                 participants=parts, summary=a.summary, recorded_at=observed_at,
                 source={"type": "manual_observation", "check": a.check, "artifact": a.artifact,
                         "observed_by": a.observed_by, "observed_at": observed_at})
    store = Store(ROOT / "data" / "records.jsonl")
    print("stored" if store.add(rec) else "already recorded (identical)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
