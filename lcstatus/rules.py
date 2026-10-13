"""Deterministic status from records. No narration, no percentages, no estimates.

For every condition of every task, the rules find the best evidence and classify it:

    satisfied            passing evidence of the right kind at the current revision
    changed_since        the last passing evidence was at an older revision — kept visible
    check_failed         the latest evidence at the current revision failed
    not_checked          a record of the right kind exists at the current revision but the
                         check produced no result (skipped, unavailable, pending, unknown)
    config_changed       nothing admitted under the current configuration or claim, but the
                         condition once passed under an earlier one — kept visible, never proof
    stale_failure        the latest evidence is a failure at an earlier revision and nothing has
                         run since — kept visible, never proof, nothing known about current code
    head_unobserved      the repository's current revision was not observed this tick, so no
                         revision relation can be claimed; the last result stays visible
    no_evidence          no record of the right kind exists for this condition at all
    inconclusive         source inspection only, which can hint but not prove

Maturity is derived from which conditions are satisfied, in this order and never higher
than the evidence allows:

    planned           nothing satisfied
    partly built      some automated conditions satisfied
    built             every automated (test / CI) condition satisfied at the current revision
    demonstrated      built, and every installed-demo condition satisfied
    ready for release demonstrated on every required platform, and every issue gate passes
    released          a published release exists

A demo whose participants' revisions no longer match current heads is shown, but as
"changed since demonstrated". Source inspection can never raise maturity above planned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any

from .catalogue import declared_participants, participants_required
from .evidence import (
    Record, check_fingerprint, condition_fingerprint, instant_key,
    record_check_fingerprint, record_condition_fingerprint, resolve_check_fingerprint,
)

AUTOMATED = ("automated_test", "ci_run")
PROVING_VERDICT = ("pass",)


def shipped_path(path: str, globs: list[str]) -> bool:
    # The shipped surface excludes evidence and tests even if a broad catalogue glob includes it.
    segments = path.split("/")
    filename = segments[-1]
    if (set(segments[:-1]) & {"docs", "contracts", "tests", "test", "plans"}
            or path.endswith((".md", ".rst")) or ".test." in filename
            or filename.startswith("test_") or filename.endswith("_test.py")):
        return False
    return any(fnmatchcase(path, pattern) for pattern in globs)


def promotion_records(app: str, row: dict[str, Any], records: list[Record], platform: str) -> list[Record]:
    return [r for r in records if r.kind == "promotion" and r.verdict == "pass"
            and r.platform == platform and row["repo"] in r.participants
            and r.source.get("observed_by", "").strip()
            and app in r.detail.get("members", {})
            and r.detail["members"][app].get("config") == check_fingerprint(row["promotion"])]


def matching_promotion(app: str, row: dict[str, Any], records: list[Record], observed: dict[str, Any],
                       platform: str = "linux") -> Record | None:
    if observed.get("missing") or observed.get("errors") or not observed.get("hashes"):
        return None
    candidates = [r for r in promotion_records(app, row, records, platform)
                  if r.detail["members"][app].get("program_hashes") == observed["hashes"]
                  and set(observed["hashes"]) == set(row["promotion"]["programs"])]
    if not candidates:
        return None
    return max(enumerate(candidates), key=lambda pair: (instant_key(pair[1].recorded_at), pair[0]))[1]


def required_promotion_checks(config: dict[str, Any], paths: list[str]) -> list[str]:
    checks = list(config["automated_checks"]) + [config["smoke_check"]]
    for gate in config["standing_gates"]:
        if any(fnmatchcase(path, pattern) for path in paths for pattern in gate["paths"]):
            checks.append(gate["check"])
    return list(dict.fromkeys(checks))


def validate_promotion_member(app: str, row: dict[str, Any], revision: str, main_history: list[str],
                              package_hashes: dict[str, str], observed: dict[str, Any],
                              gate_ids: list[str], paths: list[str], records: list[Record],
                              catalogue: dict[str, Any], participants: dict[str, str],
                              platform: str) -> None:
    """P1/P3's admission owner; every refusal happens before Store.add in the recorder."""
    if revision not in main_history:
        raise ValueError("revision is not on main first-parent history")
    if (observed["missing"] or observed["errors"] or observed["hashes"] != package_hashes
            or set(package_hashes) != set(row["promotion"]["programs"])):
        raise ValueError("installed program/package hash mismatch")
    by_id = {r.record_id: r for r in records}
    if any(record_id not in by_id for record_id in gate_ids):
        raise ValueError("missing gate record")
    supplied = [by_id[record_id] for record_id in gate_ids]
    for check_id in required_promotion_checks(row["promotion"], paths):
        check = catalogue["checks"][check_id]
        candidates = [r for r in records if r.source.get("check") == check_id
                      and r.repo == check["repo"] and r.revision == revision
                      and r.platform == check.get("platform", platform)
                      and resolve_check_fingerprint(record_check_fingerprint(r)) == check_fingerprint(check)]
        if not candidates:
            if any(r.source.get("check") == check_id and r.revision != revision for r in supplied):
                raise ValueError("gate record is for another revision")
            raise ValueError(f"missing exact-revision gate: {check_id}")
        gate = max(enumerate(candidates), key=lambda pair: (instant_key(pair[1].recorded_at), pair[0]))[1]
        allowed_kind = "ci_run" if check["runner"] == "ci_job" else (
            "installed_demo" if check["runner"] == "manual_observation" else "automated_test")
        if gate.record_id not in gate_ids or gate.kind != allowed_kind or gate.verdict != "pass":
            raise ValueError(f"missing passing latest gate record: {check_id}")
        if gate.kind == "automated_test" and (not gate.executed or gate.failed != 0):
            raise ValueError(f"gate executed no passing tests: {check_id}")
        if gate.kind == "installed_demo" and not gate.source.get("observed_by", "").strip():
            raise ValueError(f"gate is missing human observation: {check_id}")
        if participants_required(check) and gate.participants != {
                repo: participants.get(repo) for repo in declared_participants(check)}:
            raise ValueError(f"gate participants do not match this promotion: {check_id}")


def installed_statuses(catalogue: dict[str, Any], records: list[Record], *, at: str | None = None,
                       platform: str = "linux") -> list[dict[str, Any]]:
    """P2/P4/P5 labels, independent of every existing capability and public-release status."""
    statuses: dict[str, dict[str, Any]] = {}
    selected: dict[str, Record] = {}
    snapshots: dict[str, Record] = {}
    now = instant_key(at)[1] if at else datetime.now(timezone.utc).timestamp()
    for app, row in catalogue.get("apps", {}).items():
        if "promotion" not in row:
            continue
        observations = [r for r in records if r.kind == "installed_observation"
                        and r.detail.get("app") == app and r.platform == platform]
        snapshot = max(enumerate(observations), key=lambda p: (instant_key(p[1].recorded_at), p[0]))[1] if observations else None
        observed = snapshot.detail["installed"] if snapshot else {"hashes": {}, "missing": [], "errors": {"observation": "no installed observation"}}
        history = promotion_records(app, row, records, platform)
        match = matching_promotion(app, row, records, observed, platform)
        item: dict[str, Any] = {"app": app, "name": row["name"], "platform": platform,
                                "hashes": observed["hashes"], "missing": observed["missing"],
                                "errors": dict(observed["errors"]), "revision": None,
                                "promotion_id": None, "superseded": [], "lag": "unavailable",
                                "merges": [], "oldest_age_days": None, "coupled": "unavailable",
                                "current": False, "color": "red"}
        if observed["errors"]:
            item["label"] = "unavailable"
        elif observed["missing"]:
            item["label"] = "promoted build missing" if history else "not installed"
            if not history:
                item["color"] = "neutral"
        elif match is None:
            item["label"] = "unrecorded install"
        else:
            selected[app] = match
            snapshots[app] = snapshot
            item.update(label="promoted", revision=match.participants[row["repo"]],
                        promotion_id=match.record_id, color="green", coupled="clear",
                        superseded=[r.record_id for r in history if r.record_id != match.record_id])
            if snapshot.detail.get("promotion_id") != match.record_id:
                item["errors"]["observation"] = "installed facts predate this promotion record; collect again"
            for field in ("history_error", "pin_error"):
                if snapshot.detail.get(field):
                    item["errors"][field] = snapshot.detail[field]
            item["pins"] = snapshot.detail.get("pins", {})
            merges = [m for m in snapshot.detail.get("merges", [])
                      if any(shipped_path(p, row["promotion"]["shipped_paths"]) for p in m["paths"])]
            item["merges"] = merges
            if not item["errors"]:
                item["lag"] = "current"
                if merges:
                    oldest = min(instant_key(m["committed_at"])[1] for m in merges)
                    age = max(0, (now - oldest) / 86400)
                    item["oldest_age_days"] = age
                    urgent = any(set(m.get("labels", [])) & {"security", "data-loss"} for m in merges)
                    stale = urgent or age > row["promotion"]["lag_limit"]["days"]
                    item["lag"] = "stale" if stale else "behind"
                    item["color"] = "red" if stale else "amber"
            else:
                item["color"] = "red"
                item["coupled"] = "blocked" if "pin_error" in item["errors"] else "clear"
        statuses[app] = item
    # A disagreement is determined only from explicit shared locked identities, not names of
    # evidence directories. One atomic P1 set record has to cover all currently installed bytes.
    for group in catalogue.get("promotion_sets", []):
        members = [app for app in group if app in selected]
        values: dict[str, set[tuple[str, ...]]] = {}
        for app in members:
            for key, pins in statuses[app].get("pins", {}).get("values", {}).items():
                values.setdefault(key, set()).add(tuple(pins))
        disagreement = any(len(pins) > 1 or any(len(p) > 1 for p in pins) for pins in values.values())
        if not disagreement:
            continue
        same_set = len({selected[app].record_id for app in members}) == 1
        set_record = selected[members[0]]
        covered = same_set and all(
            catalogue["apps"][app]["repo"] in set_record.participants for app in members)
        for app in members:
            statuses[app]["coupled"] = "clear" if covered else "coupled-set mismatch"
            if not covered:
                statuses[app]["color"] = "red"
    for item in statuses.values():
        item["current"] = item["label"] == "promoted" and item["lag"] == "current" and item["coupled"] == "clear" and not item["errors"]
    return list(statuses.values())


@dataclass
class ConditionStatus:
    condition: dict[str, Any]
    state: str                      # satisfied | changed_since | check_failed | not_checked | stale_failure | head_unobserved | config_changed | no_evidence | inconclusive
    current: Record | None = None   # best record at the current revision, if any
    last_proven: Record | None = None
    last_result: Record | None = None   # stale_failure / head_unobserved: the latest admitted record
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
                "current": rec(self.current), "last_proven": rec(self.last_proven),
                "last_result": rec(self.last_result)}


@dataclass
class TaskStatus:
    task: dict[str, Any]
    maturity: str
    freshness: str                  # current | changed_since_verification | check_failed | not_checked | no_evidence
    conditions: list[ConditionStatus] = field(default_factory=list)
    platforms: dict[str, str] = field(default_factory=dict)   # platform -> state
    notes: list[str] = field(default_factory=list)
    release_issue_gate: str = "not_applicable"  # not_applicable | clear | blocked | unavailable
    release_issue_blockers: list[dict[str, Any]] = field(default_factory=list)
    release_issue_unavailable_repos: list[str] = field(default_factory=list)
    unobserved_repos: list[str] = field(default_factory=list)   # heads not observed this tick


def _current_head(heads: dict[str, str], repo: str) -> str | None:
    return heads.get(repo)


def _matches_current(rec: Record, heads: dict[str, str], repo: str) -> bool:
    # Admission already required the record's participant set to equal the check's declared
    # set, so "every named participant at its head" is "every declared participant at its head".
    if rec.participants:
        return all(heads.get(r) == s for r, s in rec.participants.items())
    return heads.get(repo) == rec.revision


def condition_status(
    cond: dict[str, Any], records: list[Record], heads: dict[str, str], check_repo: str,
    check: dict[str, Any],
) -> ConditionStatus:
    kind = cond["kind"]
    if not check_repo:
        raise ValueError(f"condition {cond['id']}: check {cond.get('check')!r} names no repository")
    # One definition of the declared set (catalogue.declared_participants); the writer uses the same.
    declared = declared_participants(check)
    declared_set = set(declared)
    declares_participants = participants_required(check)
    # No revision relation can be asserted unless every declared repository's head was observed.
    head_observed = all(repo in heads for repo in declared)
    want_platform = cond.get("platform") or check.get("platform")
    expected_check_fingerprint = check_fingerprint(check)
    expected_condition_fingerprint = condition_fingerprint(cond)
    same_check = [
        r for r in records
        if cond["id"] in r.condition_ids
        and r.kind == kind
        and r.source.get("check") == cond["check"]
    ]
    # Older collectors wrote app-specific release ids onto every repository. Keep those
    # append-only records from crossing product boundaries during status derivation.
    same_check = [r for r in same_check if r.repo == check_repo]
    if want_platform:
        same_check = [r for r in same_check if r.platform == want_platform]

    def admitted(r: Record) -> bool:
        # A row that names a subset, a superset, or participants for a check that declares none
        # was not produced by this configuration; it is history (config_changed), never proof.
        named = set(r.participants)
        complete = named == declared_set or (not named and not declares_participants)
        return (
            resolve_check_fingerprint(record_check_fingerprint(r)) == expected_check_fingerprint
            and record_condition_fingerprint(r, cond["id"]) == expected_condition_fingerprint
            and complete
        )

    evid = [r for r in same_check if admitted(r)]
    # Evidence gathered under a different configuration or claim.  It cannot prove anything
    # about this one, but a pass under it is history worth showing when nothing else exists.
    # Rows that carry no fingerprint at all are not attributable to any configuration.
    superseded = [
        r for r in same_check
        if not admitted(r) and record_check_fingerprint(r) is not None
    ]
    superseded_passes = [r for r in superseded if r.verdict in PROVING_VERDICT]
    # A condition that names no platform takes it from its evidence; history counts for that.
    attributed = evid or superseded_passes
    plat = want_platform or (attributed[0].platform if attributed else "n/a")

    def latest(items: list[Record], *, same_revision: bool = False) -> Record:
        # Current candidates already match the same exact head/participants. Their revision
        # timestamp must not outrank a later observation at that same code revision.
        def order(item: tuple[int, Record]) -> tuple:
            index, record = item
            if same_revision:
                return (instant_key(record.recorded_at), index)
            return (instant_key(record.revision_time), instant_key(record.recorded_at), index)

        return max(enumerate(items), key=order)[1]

    proven = [r for r in evid if r.verdict in PROVING_VERDICT]
    last_proven = latest(proven) if proven else None
    if evid and not head_observed:
        # Every state below compares revisions (an inspection's "current" included); without the
        # head none of them can be truthful.
        return ConditionStatus(cond, "head_unobserved", last_proven=last_proven, last_result=latest(evid), platform=plat)
    current = [r for r in evid if _matches_current(r, heads, check_repo)]
    if kind == "source_inspection":
        # An inspection can point at code; it cannot prove behaviour, and a miss cannot prove absence.
        return ConditionStatus(
            cond, "inconclusive", current=latest(current, same_revision=True) if current else None, platform=plat
        )
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
        last = latest(evid)
        if last.verdict == "fail":
            # the head was observed (above) and moved: the check failed and has not run since
            return ConditionStatus(cond, "stale_failure", last_result=last, platform=plat)
        return ConditionStatus(cond, "not_checked", current=None, last_proven=None, platform=plat)
    if superseded_passes:
        return ConditionStatus(cond, "config_changed", last_proven=latest(superseded_passes), platform=plat)
    return ConditionStatus(cond, "no_evidence", platform=plat)


def task_status(
    task: dict[str, Any],
    records: list[Record],
    heads: dict[str, str],
    catalogue: dict[str, Any],
    release: dict[str, Any],
) -> TaskStatus:
    checks = catalogue["checks"]
    conds: list[ConditionStatus] = []
    for c in task["conditions"]:
        chk = checks.get(c["check"], {})
        repo = chk.get("repo", "")
        conds.append(condition_status(c, records, heads, repo, chk))

    # The repositories whose heads were missing: the predicate's own inputs, not the check's repo.
    unobserved_repos = sorted({
        repo
        for c in conds if c.state == "head_unobserved"
        for repo in declared_participants(checks[c.condition["check"]])
        if repo not in heads
    })

    def proven_somewhere(c: ConditionStatus) -> bool:
        # a pass exists; its currentness is either older (changed_since) or unknown (head_unobserved)
        return c.state in ("satisfied", "changed_since") or (
            c.state == "head_unobserved" and c.last_proven is not None
        )

    automated = [c for c in conds if c.condition["kind"] in AUTOMATED]
    demos = [c for c in conds if c.condition["kind"] == "installed_demo"]
    gates = [c for c in conds if c.condition["kind"] == "issue_gate"]
    rels = [c for c in conds if c.condition["kind"] == "release_artifact"]
    sat = lambda cs: all(c.state == "satisfied" for c in cs) and bool(cs)  # noqa: E731

    issue_blockers = sorted(
        (
            issue
            for gate in gates
            if gate.current is not None and gate.current.verdict == "fail"
            for issue in gate.current.detail.get("issues", [])
        ),
        key=lambda issue: (issue.get("repo", ""), issue.get("number", 0)),
    )
    issue_unavailable = sorted({
        checks[gate.condition["check"]]["repo"]
        for gate in gates
        if gate.state not in {"satisfied", "check_failed"}
    })
    if not gates:
        issue_gate = "not_applicable"
    elif issue_unavailable:
        issue_gate = "unavailable"
    elif issue_blockers:
        issue_gate = "blocked"
    else:
        issue_gate = "clear"

    # maturity: never higher than evidence allows
    release_ready = (
        sat(automated)
        and sat(demos)
        and task.get("layer") == "release"
        and _all_platforms(conds, release)
        and (sat(gates) if gates else True)
    )
    if sat(rels) and release_ready:
        maturity = "released"
    elif release_ready:
        maturity = "ready for release"
    elif sat(automated) and demos and sat(demos):
        maturity = "demonstrated"
    elif sat(automated):
        maturity = "built"
    elif any(proven_somewhere(c) for c in automated):
        maturity = "partly built"
    elif any(c.state == "changed_since" for c in demos) and any(proven_somewhere(c) for c in automated):
        maturity = "partly built"
    else:
        maturity = "planned"

    # freshness overlay
    # Freshness is about checks. A release that does not exist is a maturity fact, not a
    # failing check, so release lookups do not count toward "check failed".
    states = [c.state if c.condition["kind"] != "release_artifact" or c.state != "check_failed" else "not_checked"
              for c in conds if c.condition["kind"] != "source_inspection"]
    # An old failure never re-run says nothing about the current revision: a check must run.
    states = ["not_checked" if s == "stale_failure" else s for s in states]
    if not states:
        freshness = "no_evidence"   # nothing checkable was defined; "current" would be a lie
    elif any(s == "check_failed" for s in states):
        freshness = "check_failed"
    elif any(s == "head_unobserved" for s in states):
        freshness = "head_unobserved"
    elif any(s in ("changed_since", "config_changed") for s in states):
        freshness = "changed_since_verification"
    elif any(s == "not_checked" for s in states):
        freshness = "not_checked"
    elif any(s == "no_evidence" for s in states):
        freshness = "no_evidence"
    else:
        freshness = "current"

    notes: list[str] = []
    if unobserved_repos:
        notes.append("The current revision of " + ", ".join(unobserved_repos)
                     + " was not observed this tick; nothing here claims a revision relation until it is.")
    if any(c.state == "changed_since" for c in demos):
        notes.append("Last demonstration was at an earlier revision; shown, not current.")
    if issue_gate == "blocked":
        notes.append("Open issues in the First Public Release milestone block readiness; evidence remains visible.")
    elif issue_gate == "unavailable":
        notes.append("First-release issue status is unavailable, so readiness fails closed.")

    return TaskStatus(
        task, maturity, freshness, conds, _platform_states(conds, release), notes,
        issue_gate, issue_blockers, issue_unavailable, unobserved_repos,
    )


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
        elif any(c.state == "head_unobserved" for c in on_p):
            out[p] = "head_unobserved"
        elif any(c.state in ("changed_since", "config_changed") for c in on_p):
            out[p] = "changed_since"
        elif any(c.state in ("not_checked", "stale_failure") for c in on_p):
            out[p] = "not_checked"
        else:
            out[p] = "no_evidence"
    return out


def _all_platforms(conds: list[ConditionStatus], release: dict[str, Any]) -> bool:
    return all(v == "satisfied" for v in _platform_states(conds, release).values())
