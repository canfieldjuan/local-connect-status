"""Deterministic status from records. No narration, no percentages, no estimates.

For every condition of every task, the rules find the best evidence and classify it:

    satisfied            passing evidence of the right kind at the current revision
    changed_since        the last passing evidence was at an older revision — kept visible
    check_failed         the latest evidence at the current revision failed
    not_checked          a record of the right kind exists at the current revision but the
                         check produced no result (skipped, unavailable, pending, unknown)
    no_evidence          no record of the right kind exists for this condition at all
    inconclusive         source inspection only, which can hint but not prove

Maturity is derived from which conditions are satisfied, in this order and never higher
than the evidence allows:

    planned           nothing satisfied
    partly built      some automated conditions satisfied
    built             every automated (test / CI) condition satisfied at the current revision
    demonstrated      built, and every installed-demo condition satisfied
    ready for release demonstrated on every required platform, and every release check passes
    released          a published release exists

A demo whose participants' revisions no longer match current heads is shown, but as
"changed since demonstrated". Source inspection can never raise maturity above planned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence import Record, check_fingerprint, instant_key, record_check_fingerprint

AUTOMATED = ("automated_test", "ci_run")
PROVING_VERDICT = ("pass",)


@dataclass
class ConditionStatus:
    condition: dict[str, Any]
    state: str                      # satisfied | changed_since | check_failed | not_checked | no_evidence | inconclusive
    current: Record | None = None   # best record at the current revision, if any
    last_proven: Record | None = None
    platform: str = "n/a"

    def as_dict(self) -> dict[str, Any]:
        def rec(r: Record | None) -> dict[str, Any] | None:
            if r is None:
                return None
            return {"record_id": r.record_id, "kind": r.kind, "verdict": r.verdict, "revision": r.revision[:12],
                    "platform": r.platform, "recorded_at": r.recorded_at, "summary": r.summary, "executed": r.executed,
                    "failed": r.failed, "skipped": r.skipped, "exit_code": r.exit_code, "source": r.source,
                    "log_path": r.log_path, "participants": {k: v[:12] for k, v in r.participants.items()},
                    "detail": r.detail}
        return {"id": self.condition["id"], "kind": self.condition["kind"], "proves": self.condition.get("proves"),
                "check": self.condition.get("check"), "state": self.state, "platform": self.platform,
                "current": rec(self.current), "last_proven": rec(self.last_proven)}


@dataclass
class TaskStatus:
    task: dict[str, Any]
    maturity: str
    freshness: str                  # current | changed_since_verification | check_failed | not_checked | no_evidence
    conditions: list[ConditionStatus] = field(default_factory=list)
    platforms: dict[str, str] = field(default_factory=dict)   # platform -> state
    notes: list[str] = field(default_factory=list)


def _current_head(heads: dict[str, str], repo: str) -> str | None:
    return heads.get(repo)


def _matches_current(rec: Record, heads: dict[str, str], repo: str) -> bool:
    if rec.participants:
        return all(heads.get(r) == s for r, s in rec.participants.items())
    # A wildcard check ("*", e.g. releases) is recorded per repository at that repository's head.
    return heads.get(repo or rec.repo) == rec.revision


def condition_status(
    cond: dict[str, Any], records: list[Record], heads: dict[str, str], check_repo: str,
    check: dict[str, Any],
) -> ConditionStatus:
    kind = cond["kind"]
    want_platform = cond.get("platform") or check.get("platform")
    expected_check_fingerprint = check_fingerprint(check)
    evid = [
        r for r in records
        if cond["id"] in r.condition_ids
        and r.kind == kind
        and r.source.get("check") == cond["check"]
        and record_check_fingerprint(r) == expected_check_fingerprint
    ]
    if check_repo:
        # Older collectors wrote app-specific release ids onto every repository. Keep those
        # append-only records from crossing product boundaries during status derivation.
        evid = [r for r in evid if r.repo == check_repo]
    if want_platform:
        evid = [r for r in evid if r.platform == want_platform]
    plat = want_platform or (evid[0].platform if evid else "n/a")

    def latest(items: list[Record], *, same_revision: bool = False) -> Record:
        # Current candidates already match the same exact head/participants. Their revision
        # timestamp must not outrank a later observation at that same code revision.
        def order(item: tuple[int, Record]) -> tuple:
            index, record = item
            if same_revision:
                return (instant_key(record.recorded_at), index)
            return (instant_key(record.revision_time), instant_key(record.recorded_at), index)

        return max(enumerate(items), key=order)[1]

    if kind == "source_inspection":
        # An inspection can point at code; it cannot prove behaviour, and a miss cannot prove absence.
        current = [r for r in evid if _matches_current(r, heads, check_repo)]
        return ConditionStatus(
            cond, "inconclusive", current=latest(current, same_revision=True) if current else None, platform=plat
        )

    current = [r for r in evid if _matches_current(r, heads, check_repo)]
    proven = [r for r in evid if r.verdict in PROVING_VERDICT]
    last_proven = latest(proven) if proven else None
    if current:
        best = latest(current, same_revision=True)
        if best.verdict in PROVING_VERDICT:
            return ConditionStatus(cond, "satisfied", current=best, last_proven=best, platform=plat)
        if best.verdict == "fail":
            return ConditionStatus(cond, "check_failed", current=best, last_proven=last_proven, platform=plat)
        # pending / skip / unavailable / unknown at current revision: not proof
        return ConditionStatus(cond, "not_checked", current=best, last_proven=last_proven, platform=plat)
    if last_proven is not None:
        return ConditionStatus(cond, "changed_since", last_proven=last_proven, platform=plat)
    if evid:
        # records exist, but none at the current revision and none ever passed
        return ConditionStatus(cond, "not_checked", current=None, last_proven=None, platform=plat)
    return ConditionStatus(cond, "no_evidence", platform=plat)


def task_status(task: dict[str, Any], records: list[Record], heads: dict[str, str], catalogue: dict[str, Any], release: dict[str, Any]) -> TaskStatus:
    checks = catalogue["checks"]
    conds: list[ConditionStatus] = []
    for c in task["conditions"]:
        chk = checks.get(c["check"], {})
        repo = chk.get("repo") if chk.get("repo") not in (None, "*") else task.get("app_repo", "")
        conds.append(condition_status(c, records, heads, repo, chk))

    automated = [c for c in conds if c.condition["kind"] in AUTOMATED]
    demos = [c for c in conds if c.condition["kind"] == "installed_demo"]
    rels = [c for c in conds if c.condition["kind"] == "release_artifact"]
    sat = lambda cs: all(c.state == "satisfied" for c in cs) and bool(cs)  # noqa: E731

    # maturity: never higher than evidence allows
    release_ready = (
        sat(automated) and sat(demos) and task.get("layer") == "release" and _all_platforms(conds, release)
    )
    if sat(rels) and release_ready:
        maturity = "released"
    elif release_ready:
        maturity = "ready for release"
    elif sat(automated) and demos and sat(demos):
        maturity = "demonstrated"
    elif sat(automated):
        maturity = "built"
    elif any(c.state in ("satisfied", "changed_since") for c in automated):
        maturity = "partly built"
    elif any(c.state == "changed_since" for c in demos) and any(c.state in ("satisfied", "changed_since") for c in automated):
        maturity = "partly built"
    else:
        maturity = "planned"

    # freshness overlay
    # Freshness is about checks. A release that does not exist is a maturity fact, not a
    # failing check, so release lookups do not count toward "check failed".
    states = [c.state if c.condition["kind"] != "release_artifact" or c.state != "check_failed" else "not_checked"
              for c in conds if c.condition["kind"] != "source_inspection"]
    if not states:
        freshness = "no_evidence"   # nothing checkable was defined; "current" would be a lie
    elif any(s == "check_failed" for s in states):
        freshness = "check_failed"
    elif any(s == "changed_since" for s in states):
        freshness = "changed_since_verification"
    elif any(s == "not_checked" for s in states):
        freshness = "not_checked"
    elif any(s == "no_evidence" for s in states):
        freshness = "no_evidence"
    else:
        freshness = "current"

    notes: list[str] = []
    if any(c.state == "changed_since" for c in demos):
        notes.append("Last demonstration was at an earlier revision; shown, not current.")

    return TaskStatus(task, maturity, freshness, conds, _platform_states(conds, release), notes)


def _platform_states(conds: list[ConditionStatus], release: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in release.get("required_platforms", []):
        on_p = [c for c in conds if c.platform == p and c.condition["kind"] != "source_inspection"]
        if not on_p or all(c.state == "no_evidence" for c in on_p):
            out[p] = "no_evidence"
        elif all(c.state == "satisfied" for c in on_p):
            out[p] = "satisfied"
        elif any(c.state == "check_failed" for c in on_p):
            out[p] = "check_failed"
        elif any(c.state == "changed_since" for c in on_p):
            out[p] = "changed_since"
        elif any(c.state == "not_checked" for c in on_p):
            out[p] = "not_checked"
        else:
            out[p] = "no_evidence"
    return out


def _all_platforms(conds: list[ConditionStatus], release: dict[str, Any]) -> bool:
    return all(v == "satisfied" for v in _platform_states(conds, release).values())
