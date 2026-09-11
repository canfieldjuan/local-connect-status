"""status.json, report.md and dashboard.html from the same derived statuses.

The dashboard embeds the JSON it renders, so it works from a file:// URL and from the
static server alike, and it never reaches for a credential. Wording is fixed templates.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .evidence import Record, atomic_write, instant_key
from .rules import TaskStatus

MATURITY_ORDER = ["planned", "partly built", "built", "demonstrated", "ready for release", "released"]
FRESH_LABEL = {
    "current": "verified at current code",
    "changed_since_verification": "changed since verification",
    "check_failed": "check failed",
    "not_checked": "a check ran but gave no result",
    "no_evidence": "no evidence",
}
COND_LABEL = {
    "satisfied": "verified",
    "changed_since": "changed since verification",
    "check_failed": "check failed",
    "not_checked": "check skipped or unavailable",
    "no_evidence": "no evidence",
    "inconclusive": "needs verification",
}
LAYER_LABEL = {"standalone": "Standalone", "connect": "Connect", "automate": "Automate", "release": "Release"}
LAYER_BLURB = {
    "standalone": "What the app does by itself.",
    "connect": "Work handed between apps when you start it.",
    "automate": "Work that begins from a rule without a click, with any confirmation you must give afterward.",
    "release": "What has to be true before anyone can download it.",
}


def next_action(status: TaskStatus) -> str | None:
    prefixes = {
        "check_failed": "Investigate",
        "changed_since": "Re-run at current code",
        "not_checked": "Complete the check",
        "no_evidence": "Collect evidence",
        "inconclusive": "Add behavioral proof beyond source inspection",
    }
    for state in prefixes:
        condition = next((c for c in status.conditions if c.state == state), None)
        if condition is not None:
            return f"{prefixes[state]} ({condition.condition['check']}): {condition.condition.get('proves', '')}"
    return None


def markdown_cell(value: object) -> str:
    return str(value).replace("|", r"\|").replace("\n", "<br>")


def condition_payload(status: Any) -> dict[str, Any]:
    condition = status.as_dict()
    label = COND_LABEL[condition["state"]]
    if condition["kind"] == "release_artifact" and condition["state"] == "check_failed":
        evidence = condition["current"] or condition["last_proven"] or {}
        missing = evidence.get("detail", {}).get("missing", [])
        label = "release incomplete" if missing else "no release published"
    condition["label"] = label
    return condition


def status_payload(cat: dict[str, Any], statuses: list[TaskStatus], heads: dict[str, str],
                   failures: list[dict[str, Any]], state: dict[str, Any], changed: dict[str, tuple[str, str]],
                   records: list[Record]) -> dict[str, Any]:
    revs = {}
    for r in records:
        if r.kind == "revision" and heads.get(r.repo) == r.revision:
            revs[r.repo] = {"sha": r.revision, "committed_at": r.revision_time, "subject": r.summary}
    changes = [r for r in records if r.kind == "change"]
    latest_changes = sorted(changes, key=lambda r: (instant_key(r.revision_time), instant_key(r.recorded_at)), reverse=True)[:10]
    return {
        "generated_at": state.get("last_run_at"),
        "collection_run": state.get("runs"),
        "release": cat["release"],
        "heads": {repo: revs.get(repo, {"sha": sha, "committed_at": None, "subject": None}) for repo, sha in heads.items()},
        "source_failures": failures,
        "recent_changes": [
            {"repo": r.repo, "from": r.detail.get("old") or r.summary.split(" -> ")[0], "to": r.revision[:12],
             "summary": r.summary, "affected_tasks": r.task_ids, "unmapped_files": r.detail.get("unmapped_files", []),
             "docs_only": r.detail.get("docs_only"), "touches_contract_text": r.detail.get("touches_contract_text"),
             "commits": r.detail.get("commits", [])[:8],
             "commits_complete": r.detail.get("commits_complete", True), "recorded_at": r.recorded_at}
            for r in latest_changes
        ],
        "tasks": [
            {
                "id": s.task["id"], "app": s.task["app"], "layer": s.task["layer"], "title": s.task["title"],
                "promise": s.task["promise"], "human_involvement": s.task.get("human_involvement"),
                "next_action": next_action(s),
                "maturity": s.maturity, "freshness": s.freshness, "platforms": s.platforms, "notes": s.notes,
                "conditions": [condition_payload(c) for c in s.conditions],
                "depends_on": s.task.get("depends_on", []),
            }
            for s in statuses
        ],
    }


def render_all(site: Path, cat: dict[str, Any], statuses: list[TaskStatus], records: list[Record],
               heads: dict[str, str], failures: list[dict[str, Any]], state: dict[str, Any],
               changed: dict[str, tuple[str, str]]) -> None:
    site = Path(site)
    payload = status_payload(cat, statuses, heads, failures, state, changed, records)
    atomic_write(site / "status.json", json.dumps(payload, indent=2))
    atomic_write(site / "report.md", report_md(payload, cat))
    atomic_write(site / "dashboard.html", dashboard_html(payload, cat))
    atomic_write(site / "index.html", dashboard_html(payload, cat))


# ---------------------------------------------------------------------------- markdown

def report_md(p: dict[str, Any], cat: dict[str, Any]) -> str:
    L: list[str] = []
    w = L.append
    w("# Local Connect — what works, what's left")
    w("")
    w(f"Generated {p['generated_at']} (collection run {p['collection_run']}). Release target: **{p['release']['target']}**.")
    w("")
    if p["source_failures"]:
        w("> **Some sources could not be read this run.** Affected rows are not treated as freshly verified; each row shows the applicable current attempt or last proven result.")
        for f in p["source_failures"]:
            w(f"> - {f['repo']}: {f['what']} — {f['why']}")
        w("")
    w("## Where the code is")
    w("")
    w("| Repository | Head | Commit |")
    w("|---|---|---|")
    for repo, h in p["heads"].items():
        w(f"| {repo} | `{(h['sha'] or '')[:12]}` | {h.get('subject') or '—'} |")
    w("")
    sc = p["release"]["automate_scope"]
    w("## Unresolved launch-scope decision")
    w("")
    w(f"Automate scope for the first release: **{sc['decision']}**. {sc['note']}")
    w("")
    for layer in ("standalone", "connect", "automate", "release"):
        w(f"## {LAYER_LABEL[layer]} — {LAYER_BLURB[layer]}")
        w("")
        for t in [t for t in p["tasks"] if t["layer"] == layer]:
            plat = ", ".join(f"{k}: {COND_LABEL.get(v, v)}" for k, v in t["platforms"].items())
            w(f"### {t['title']}")
            w("")
            w(f"**{t['maturity']}** · {FRESH_LABEL[t['freshness']]}" + (f" · {plat}" if plat else ""))
            w("")
            w(f"*Promise.* {t['promise']}")
            w("")
            if t.get("human_involvement"):
                w(f"*Your part.* {t['human_involvement']}")
                w("")
            if t.get("unfinished"):
                w(f"*Unfinished.* {t['unfinished']}")
                w("")
            if t.get("next_action"):
                w(f"*Next useful action.* {t['next_action']}")
                w("")
            w("| Condition | State | Evidence |")
            w("|---|---|---|")
            for c in t["conditions"]:
                ev = c["current"] or c["last_proven"]
                evs = "—"
                if ev:
                    evs = f"{ev['kind']} `{ev['revision']}` {ev['verdict']} {ev['recorded_at'][:10]}"
                    if ev.get("executed") is not None:
                        evs += f" ({ev['executed']} run, {ev.get('failed') or 0} failed, {ev.get('skipped') or 0} skipped)"
                    if ev.get("summary"):
                        evs += f" — {ev['summary']}"
                w(f"| {markdown_cell(c['proves'])} | {c['label']} | {markdown_cell(evs)} |")
            w("")
    if p["recent_changes"]:
        w("## Recent changes observed")
        w("")
        for c in p["recent_changes"]:
            w(f"- **{c['repo']}** {c['summary']} — affected: {', '.join(c['affected_tasks']) or 'none mapped'}"
              + ("; commit list unavailable, retry pending" if not c.get("commits_complete", True) else "")
              + (f"; unmapped files needing assessment: {len(c['unmapped_files'])}" if c["unmapped_files"] else "")
              + (" (docs only)" if c.get("docs_only") else ""))
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------- html

CSS = """
:root{--bg:#f6f4ee;--card:#fffdf8;--ink:#1c1b18;--soft:#4d4a44;--mute:#7c776d;--rule:#d9d2c3;
--ok:#2d6b3a;--okbg:#e2ecdf;--warn:#8a5a0c;--warnbg:#f3e7cf;--bad:#8f2d3f;--badbg:#f1dede;--info:#3f5a6b;--infobg:#dfe7ec;--accent:#c8471b}
@media (prefers-color-scheme:dark){:root{--bg:#16140f;--card:#1f1c15;--ink:#f0ebe0;--soft:#c2bbac;--mute:#8d8578;--rule:#3a352b;
--ok:#7fb98a;--okbg:#1e2a1f;--warn:#d9a04a;--warnbg:#2c2416;--bad:#e08a97;--badbg:#301b1f;--info:#93bcd2;--infobg:#17242b;--accent:#e8642f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px 20px 80px}h1{font-size:30px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:20px;margin:36px 0 6px}h3{font-size:17px;margin:0}.sub{color:var(--soft);margin:0 0 14px}
nav{display:flex;gap:6px;flex-wrap:wrap;margin:18px 0 8px}nav a{padding:6px 12px;border:1px solid var(--rule);border-radius:999px;color:var(--ink);text-decoration:none;font-size:13px}
nav a.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.banner{border:1px solid var(--rule);border-left:4px solid var(--info);background:var(--infobg);padding:10px 14px;margin:10px 0;font-size:14px}
.banner.bad{border-left-color:var(--bad);background:var(--badbg)}.banner.warn{border-left-color:var(--warn);background:var(--warnbg)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:16px;min-width:0;overflow:hidden}
.evidence{overflow-x:auto;max-width:100%}.evidence table{min-width:0}.evidence td{overflow-wrap:anywhere}
.pill{display:inline-block;font:600 11px/1 system-ui;letter-spacing:.06em;text-transform:uppercase;padding:5px 8px;border-radius:4px;border:1px solid;margin-right:6px;white-space:nowrap}
/* A card whose evidence is open takes the whole row: a three-column table does not fit a grid cell. */
.grid>.card:has(details[open]){grid-column:1/-1}.evidence table{table-layout:auto}.evidence td:nth-child(2){white-space:nowrap}
.p-ok{color:var(--ok);border-color:var(--ok);background:var(--okbg)}.p-warn{color:var(--warn);border-color:var(--warn);background:var(--warnbg)}
.p-bad{color:var(--bad);border-color:var(--bad);background:var(--badbg)}.p-mute{color:var(--mute);border-color:var(--rule)}.p-info{color:var(--info);border-color:var(--info);background:var(--infobg)}
.meta{color:var(--mute);font-size:12.5px}.field{margin:10px 0 0}.field b{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin-bottom:2px}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--rule);vertical-align:top}th{color:var(--mute);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em}
details{margin-top:10px;min-width:0}summary{cursor:pointer;color:var(--info);font-size:13.5px}code{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;background:rgba(127,127,127,.12);padding:1px 4px;border-radius:3px;overflow-wrap:anywhere}
.plat{display:inline-block;font-size:12px;padding:3px 8px;border:1px solid var(--rule);border-radius:4px;margin:4px 6px 0 0}
a{color:var(--accent)}.hidden{display:none}footer{margin-top:40px;color:var(--mute);font-size:12.5px}
"""

JS = """
const DATA = __DATA__;
const pillFor = (s)=>({satisfied:'p-ok',verified:'p-ok',current:'p-ok',changed_since:'p-warn',changed_since_verification:'p-warn',check_failed:'p-bad',not_checked:'p-mute',no_evidence:'p-mute',inconclusive:'p-info',pending:'p-mute'}[s]||'p-mute');
const matPill = (m)=>({'released':'p-ok','ready for release':'p-ok','demonstrated':'p-ok','built':'p-info','partly built':'p-warn','planned':'p-mute'}[m]||'p-mute');
const FRESH = {current:'verified at current code',changed_since_verification:'changed since verification',check_failed:'check failed',not_checked:'a check ran but gave no result',no_evidence:'no evidence'};
const COND = {satisfied:'verified',changed_since:'changed since verification',check_failed:'check failed',not_checked:'check skipped or unavailable',no_evidence:'no evidence',inconclusive:'needs verification'};
const condLabel = (c)=>c.label || COND[c.state];
const esc = (s)=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function evLine(e){ if(!e) return '—'; let s=`${esc(e.kind)} <code>${esc(e.revision)}</code> ${esc(e.verdict)} ${esc(String(e.recorded_at).slice(0,10))}`;
  if(e.executed!=null) s+=` · ${esc(e.executed)} run, ${esc(e.failed||0)} failed, ${esc(e.skipped||0)} skipped`;
  if(e.exit_code!=null) s+=` · exit ${esc(e.exit_code)}`; if(e.platform && e.platform!=='n/a') s+=` · ${esc(e.platform)}`;
  if(e.summary) s+=` · ${esc(e.summary)}`;
  if(e.participants && Object.keys(e.participants).length) s+=` · with `+Object.entries(e.participants).map(([r,v])=>`${esc(r)}@${esc(v)}`).join(', ');
  if(e.source && e.source.url) s+=` · <a href="${esc(e.source.url)}" target="_blank" rel="noopener">GitHub job</a>`;
  if(e.log_path){const name=String(e.log_path).split('/').pop(); s+=` · <span class="meta" title="${esc(e.log_path)}">log: ${esc(name)}</span>`;} return s; }
function taskCard(t){
  const plats = Object.entries(t.platforms).map(([p,s])=>`<span class="plat"><b>${esc(p)}</b>: ${esc(COND[s]||s)}</span>`).join('');
  const conds = t.conditions.map(c=>{const ev=c.current||c.last_proven; return `<tr><td>${esc(c.proves)}<div class="meta">${esc(c.kind)} · check <code>${esc(c.check)}</code>${c.platform&&c.platform!=='n/a'?' · '+esc(c.platform):''}</div></td><td><span class="pill ${pillFor(c.state)}">${esc(condLabel(c))}</span>${c.state==='changed_since'&&c.last_proven?`<div class="meta">last proven at <code>${esc(c.last_proven.revision)}</code></div>`:''}</td><td>${evLine(ev)}</td></tr>`}).join('');
  const deps = (t.depends_on||[]).map(d=>`<li><code>${esc(d.repo)}</code>: ${d.paths.map(p=>'<code>'+esc(p)+'</code>').join(', ')}</li>`).join('');
  return `<div class="card" id="task-${esc(t.id)}"><h3>${esc(t.title)}</h3>
   <div style="margin:8px 0"><span class="pill ${matPill(t.maturity)}">${esc(t.maturity)}</span><span class="pill ${pillFor(t.freshness)}">${esc(FRESH[t.freshness])}</span></div>
   <div>${plats}</div>
   <div class="field"><b>Promise</b>${esc(t.promise)}</div>
   ${t.human_involvement?`<div class="field"><b>What still needs you</b>${esc(t.human_involvement)}</div>`:''}
   ${t.unfinished?`<div class="field"><b>Unfinished</b>${esc(t.unfinished)}</div>`:''}
   ${t.notes&&t.notes.length?`<div class="field"><b>Notes</b>${t.notes.map(esc).join('<br>')}</div>`:''}
   ${t.next_action?`<div class="field"><b>Next useful action</b>${esc(t.next_action)}</div>`:''}
   <details><summary>Evidence and technical detail</summary>
     <div class="evidence"><table><thead><tr><th>What must be true</th><th>State</th><th>Dated evidence</th></tr></thead><tbody>${conds}</tbody></table></div>
     <div class="field"><b>Code this depends on</b><ul style="margin:4px 0 0 18px;padding:0">${deps}</ul></div>
   </details></div>`; }
function render(view){
  const root=document.getElementById('root'); const p=DATA;
  document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('on',a.dataset.view===view));
  let h='';
  if(p.source_failures&&p.source_failures.length){h+=`<div class="banner bad"><b>Some sources could not be read on the last run.</b> Affected rows are not treated as freshly verified; each row shows the applicable current attempt or last proven result.<ul style="margin:6px 0 0 18px">${p.source_failures.map(f=>`<li>${esc(f.repo)}: ${esc(f.what)} — ${esc(f.why)}</li>`).join('')}</ul></div>`;}
  const stale = p.tasks.filter(t=>t.freshness==='changed_since_verification').length, failed=p.tasks.filter(t=>t.freshness==='check_failed').length;
  if(failed) h+=`<div class="banner bad"><b>${failed} task(s) have a failing check</b> at the current code.</div>`;
  if(stale) h+=`<div class="banner warn"><b>${stale} task(s) changed since they were last verified.</b> The last proven result stays visible; it is not a current result.</div>`;
  const sc=p.release.automate_scope; h+=`<div class="banner"><b>Unresolved launch-scope decision:</b> which Automate tasks are required for the first release is <b>${esc(sc.decision)}</b>. ${esc(sc.note)}</div>`;
  const apps={'email-watcher':'Email Watcher','document-summarizer':'Document Summarizer','invoice-processor':'Invoice Processor'};
  const show = view==='overview'? p.tasks : view==='release' ? p.tasks.filter(t=>t.layer==='release') : p.tasks.filter(t=>t.app===view);
  if(view==='overview'){
    h+=`<h2>Where the code is</h2><table><thead><tr><th>Repository</th><th>Head</th><th>Commit</th></tr></thead><tbody>${Object.entries(p.heads).map(([r,x])=>`<tr><td>${esc(r)}</td><td><code>${esc((x.sha||'').slice(0,12))}</code></td><td>${esc(x.subject||'—')}</td></tr>`).join('')}</tbody></table>`;
    if(p.recent_changes.length){h+=`<h2>Recent changes observed</h2><ul>${p.recent_changes.map(c=>`<li><b>${esc(c.repo)}</b> ${esc(c.summary)} — affected: ${c.affected_tasks.length?c.affected_tasks.map(esc).join(', '):'none mapped'}${!c.commits_complete?' · <span class="pill p-bad">commit list unavailable; retry pending</span>':''}${c.unmapped_files.length?` · <span class="pill p-warn">${c.unmapped_files.length} unmapped file(s) need assessment</span>`:''}${c.docs_only?' · <span class="pill p-info">docs only</span>':''}${c.touches_contract_text?' · <span class="pill p-info">contract text</span>':''}</li>`).join('')}</ul>`;}
  }
  for(const layer of ['standalone','connect','automate','release']){
    const ts=show.filter(t=>t.layer===layer); if(!ts.length) continue;
    const L={standalone:['Standalone','What the app does by itself.'],connect:['Connect','Work handed between apps when you start it.'],automate:['Automate','Work that begins from a rule without a click, with any confirmation you must give afterward.'],release:['Before we ship','What has to be true before anyone can download it.']}[layer];
    h+=`<h2>${L[0]}</h2><p class="sub">${L[1]}</p><div class="grid">${ts.map(taskCard).join('')}</div>`;
  }
  root.innerHTML=h;
}
document.querySelectorAll('nav a').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();location.hash=a.dataset.view;render(a.dataset.view);}));
render((location.hash||'#overview').slice(1));
"""


def dashboard_html(p: dict[str, Any], cat: dict[str, Any]) -> str:
    # Escape every HTML-significant code point before embedding JSON in a script block.
    data = (json.dumps(p).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))
    gen = html.escape(p.get("generated_at") or "never")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local Connect — What works. What's left.</title><style>{CSS}</style></head><body><div class="wrap">
<p class="meta">Local Connect / product progress · release target: {html.escape(p['release']['target'])} · generated {gen} (run {p.get('collection_run')})</p>
<h1>What works. What's left.</h1>
<p class="sub">The apps, the connections between them, and the work they will do for you automatically — with the evidence for each claim.</p>
<nav><a href="#overview" data-view="overview">Overview</a><a href="#email-watcher" data-view="email-watcher">Email Watcher</a><a href="#document-summarizer" data-view="document-summarizer">Document Summarizer</a><a href="#invoice-processor" data-view="invoice-processor">Invoice Processor</a><a href="#release" data-view="release">Before we ship</a></nav>
<div id="root"></div>
<footer>Every row is derived from recorded evidence in <code>data/records.jsonl</code>; nothing here is written by hand. "Verified" means a check passed at the exact current code. A change to the code makes earlier evidence "changed since verification" until a check runs again. Release status comes from the release-evidence rows above.</footer>
</div><script>{JS.replace('__DATA__', data)}</script></body></html>
"""
