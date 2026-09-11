"""The collection run: observe heads, detect change, verify, store, render. One command.

    python -m lcstatus.collect                 # routine run (timer)
    python -m lcstatus.collect --heavy         # also run the Rust suite (slow)
    python -m lcstatus.collect --checks ip.pytest.ledger_via_connect xapp.accept_ew_to_ip
    python -m lcstatus.collect --set-baseline eom-email-watcher=<sha>   # then the next run observes the real move
    python -m lcstatus.collect --render-only

Exit status: 0 when every source was read; 2 when some source failed (the report says which).
A failed source never looks like a clean empty result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import catalogue as catmod
from .change import assess
from .evidence import Record, Store, atomic_write, now_iso
from .render import render_all
from .rules import task_status
from .sources import Failure, GitHub, Mirrors, Revision
from .verify import Runner

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"
SITE = ROOT / "site"


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"heads": {}, "runs": 0}


HEAD_FAILURES = {"head", "git_head", "github_head_mismatch"}


def render_heads(state: dict[str, Any]) -> dict[str, str]:
    unknown = set(state.get("unknown_heads", []))
    if "unknown_heads" not in state:
        unknown.update(
            f.get("repo")
            for f in state.get("last_failures", [])
            if f.get("what") in HEAD_FAILURES
        )
    return {repo: sha for repo, sha in state.get("heads", {}).items() if repo not in unknown}


def release_targets(check: dict[str, Any], revs: dict[str, Revision]) -> list[tuple[str, Revision]]:
    repo = check["repo"]
    if repo == "*":
        return list(revs.items())
    rev = revs.get(repo)
    return [(repo, rev)] if rev is not None else []


def store_result(store: Store, failures: list[dict[str, Any]], rec: Record) -> None:
    store.add(rec)
    if rec.verdict == "unavailable":
        source_type = rec.source.get("type") or rec.kind
        failure = {"repo": rec.repo, "what": source_type, "why": rec.summary or "unavailable"}
        if failure not in failures:
            failures.append(failure)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lcstatus.collect")
    ap.add_argument("--catalogue", default=str(ROOT / "catalogue.json"))
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--site", default=str(SITE))
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--heavy", action="store_true", help="run checks marked heavy (Rust suite)")
    ap.add_argument("--checks", nargs="*", help="only these check ids (plus CI/release reads)")
    ap.add_argument("--no-local", action="store_true", help="skip local runners; read CI and releases only")
    ap.add_argument("--set-baseline", nargs="*", default=[], metavar="repo=sha",
                    help="write these heads into state so the next run observes the real change")
    ap.add_argument("--render-only", action="store_true")
    args = ap.parse_args(argv)

    cat = catmod.load(Path(args.catalogue))
    data = Path(args.data)
    data.mkdir(parents=True, exist_ok=True)
    # One collector at a time. A second run (timer tick, or a baseline write during a run)
    # must never interleave with a run in progress; state.json would lose one of them.
    import fcntl
    lock_fh = open(data / ".lock", "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | (0 if args.set_baseline else fcntl.LOCK_NB))
    except BlockingIOError:
        print("another collection is running; not starting a second one", file=sys.stderr)
        return 3
    store = Store(data / "records.jsonl")
    state_path = data / "state.json"
    state = load_state(state_path)
    failures: list[dict[str, Any]] = []

    if args.set_baseline:
        for item in args.set_baseline:
            repo, sha = item.split("=", 1)
            state["heads"][repo] = sha
        atomic_write(state_path, json.dumps(state, indent=2))
        print(f"baseline set: {args.set_baseline}")
        return 0

    mirrors = Mirrors(CACHE / "mirrors", cat["repos"])
    gh = GitHub()
    runner = Runner(mirrors, CACHE, data / "logs", gh, cat)

    heads: dict[str, str] = {}
    revs: dict[str, Revision] = {}
    changed: dict[str, tuple[str, str]] = {}

    if not args.render_only:
        # ---- observe ------------------------------------------------------------------
        for repo in cat["repos"]:
            stale_note = None
            fetch_confirmed = False
            if not args.no_fetch:
                f = mirrors.fetch(repo)
                if isinstance(f, Failure):
                    failures.append({"repo": repo, "what": f.what, "why": f.why})
                    stale_note = f"fetch failed: {f.why}"
                    store.add(Record(kind="collection_failure", repo=repo, revision=state["heads"].get(repo, ""),
                                     verdict="unavailable", summary=stale_note, source={"type": "git_fetch"}))
                else:
                    fetch_confirmed = True
            head = mirrors.head(repo)
            if isinstance(head, Failure):
                failures.append({"repo": repo, "what": head.what, "why": head.why})
                store.add(Record(kind="collection_failure", repo=repo, revision="", verdict="unavailable",
                                 summary=f"{head.what}: {head.why}", source={"type": "git_head"}))
                # The current head is unknown. It must stay unknown: supplying the last known
                # SHA would let stored passing evidence read "verified at current code" for a
                # revision this run never observed. Without a head, the rules can only show
                # earlier evidence as historical.
                continue
            # Cross-check the mirror against GitHub's default branch before treating it as
            # current. A mismatch proves this extracted tree is stale.
            api = gh.default_branch_head(cat["repos"][repo]["github"])
            if isinstance(api, Failure):
                failures.append({"repo": repo, "what": "github_head", "why": api.why})
                store.add(Record(kind="collection_failure", repo=repo, revision=head.sha, verdict="unavailable",
                                 summary=f"GitHub head lookup failed: {api.why}", source={"type": "github_api"}))
                if not fetch_confirmed:
                    continue
            elif api.get("sha") and api["sha"] != head.sha:
                why = f"mirror {head.sha[:12]} != GitHub {api['sha'][:12]}; mirror may lag"
                failures.append({"repo": repo, "what": "github_head_mismatch", "why": why})
                store.add(Record(kind="collection_failure", repo=repo, revision=head.sha, verdict="partial",
                                 summary=why, source={"type": "github_api"},
                                 detail={"github_sha": api["sha"]}))
                continue
            revs[repo] = head
            heads[repo] = head.sha
            rec = Record(kind="revision", repo=repo, revision=head.sha, revision_time=head.committed_at, verdict="pass",
                         summary=head.subject, source={"type": "mirror", "stale": stale_note},
                         detail={"branch": "main"})
            store.add(rec)
            prev = state["heads"].get(repo)
            if prev and prev != head.sha:
                changed[repo] = (prev, head.sha)
                files = mirrors.changed_files(repo, prev, head.sha)
                if isinstance(files, Failure):
                    # A diff we could not compute is a failure to observe, never "no changes".
                    failures.append({"repo": repo, "what": "diff", "why": files.why})
                    store.add(Record(kind="collection_failure", repo=repo, revision=head.sha, revision_time=head.committed_at,
                                     verdict="unavailable", summary=f"diff {prev[:12]}..{head.sha[:12]} failed: {files.why}",
                                     source={"type": "mirror_diff"}, detail={"old": prev}))
                else:
                    a = assess(repo, prev, head.sha, files, cat["tasks"])
                    store.add(Record(kind="change", repo=repo, revision=head.sha, revision_time=head.committed_at, verdict="pass",
                                     task_ids=sorted(a.affected_tasks), summary=f"{prev[:12]} -> {head.sha[:12]}: {len(files)} files",
                                     source={"type": "mirror_diff"},
                                     detail=dict(a.as_detail(), old=prev, commits=mirrors.commits_between(repo, prev, head.sha)[:50])))

        # ---- decide what to verify --------------------------------------------------
        cond_map: dict[str, tuple[list[str], list[str]]] = {}
        for t in cat["tasks"]:
            for c in t["conditions"]:
                conds, tasks = cond_map.setdefault(c["check"], ([], []))
                conds.append(c["id"]); tasks.append(t["id"])

        def wanted(cid: str, chk: dict[str, Any]) -> bool:
            if args.checks is not None:
                return cid in args.checks
            if chk.get("heavy") and not args.heavy:
                return False
            return True

        # ---- verify -----------------------------------------------------------------
        ci_by_repo: dict[str, dict[str, dict[str, Any]]] = {}
        for cid, chk in cat["checks"].items():
            runner_kind = chk["runner"]
            conds, tasks = cond_map.get(cid, ([], []))
            if runner_kind == "ci_job":
                ci_by_repo.setdefault(chk["repo"], {})[cid] = chk
                continue
            if runner_kind == "github_release":
                for repo, rev in release_targets(chk, revs):
                    result = runner.releases(
                        repo, rev, conds, tasks, required_assets=chk.get("required_assets")
                    )
                    store_result(store, failures, result)
                continue
            if runner_kind == "manual_observation":
                continue   # only a person records these, via scripts/record_observation.py
            if args.no_local or not wanted(cid, chk):
                continue
            if runner_kind == "source_inspection":
                rev = revs.get(chk["repo"])
                if rev is None:
                    continue
                store_result(store, failures, runner.source_inspection(cid, chk, rev, conds, tasks))
            elif runner_kind == "pytest":
                rev = revs.get(chk["repo"])
                if rev is None:
                    continue
                print(f"running {cid} @ {rev.sha[:12]} ...", flush=True)
                r = runner.pytest(cid, chk, rev, conds, tasks)
                store_result(store, failures, r)
                print(f"  {r.verdict}: {r.summary}", flush=True)
            elif runner_kind == "cargo_lib":
                rev = revs.get(chk["repo"])
                if rev is None:
                    continue
                print(f"running {cid} @ {rev.sha[:12]} (heavy) ...", flush=True)
                r = runner.cargo_lib(cid, chk, rev, conds, tasks)
                store_result(store, failures, r)
                print(f"  {r.verdict}: {r.summary}", flush=True)
            elif runner_kind == "accept_ew_ip":
                if all(p in revs for p in chk["participants"]):
                    print(f"running {cid} ...", flush=True)
                    r = runner.accept_ew_ip(cid, chk, revs, conds, tasks)
                    store_result(store, failures, r)
                    print(f"  {r.verdict}: {r.summary}", flush=True)
        for repo, checks in ci_by_repo.items():
            rev = revs.get(repo)
            if rev is None:
                continue
            for r in runner.ci_jobs(repo, rev, checks, cond_map):
                store_result(store, failures, r)

        state["heads"].update(heads)
        state["unknown_heads"] = sorted(set(cat["repos"]) - set(heads))
        state["runs"] = state.get("runs", 0) + 1
        state["last_run_at"] = now_iso()
        state["last_failures"] = failures
        atomic_write(state_path, json.dumps(state, indent=2))
    else:
        heads = render_heads(state)
        failures = state.get("last_failures", [])

    # ---- derive & render ----------------------------------------------------------
    records = store.all()
    statuses = [task_status(t, records, heads, cat, cat["release"]) for t in cat["tasks"]]
    render_all(Path(args.site), cat, statuses, records, heads, failures, state, changed)
    print(f"rendered {args.site} ({len(records)} records; {len(failures)} source failures)")
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
