"""Execute the rendered page script with fixture DOM/fetch/clock; no live browser or network."""
import json
import shutil
import subprocess
from pathlib import Path

from lcstatus.render import JS, dashboard_html, status_payload


def test_page_refresh_preserves_view_scroll_and_details_and_marks_age_and_failed_fetch(tmp_path: Path):
    cat = {'release': {'target': 'fixture', 'automate_scope': {'decision': 'fixture', 'note': 'fixture'}}}
    payload = status_payload(cat, [], {}, [], {'last_run_at': '2026-10-13T12:00:00+00:00'}, {}, [])
    payload['rendered_at'] = 'fixture-first'
    payload['installed_apps'] = [{'app':'email-watcher','name':'Fixture watcher','color':'neutral',
                                 'label':'not installed','revision':None,'coupled_color':'neutral',
                                 'coupled':'not compared','merges':[],'lag':'no promotion'}]
    script = JS.replace('__DATA__', json.dumps(payload))
    harness = r'''
const vm=require('node:vm');const assert=require('node:assert/strict');
let now=Date.parse('2026-10-13T12:01:00Z'),calls=[],timers=[],scroll=[];
const elements={root:{innerHTML:'',hidden:false},freshness:{className:'',textContent:''},
                'task-fixture':{querySelector:()=>detail},
                'installed-email-watcher':{querySelector:()=>installedDetail}};
const detail={open:false},installedDetail={open:false};
let next={...PAYLOAD,rendered_at:'fixture-second',heads:{changed:{sha:'b'.repeat(40)}}};
let failed=false;
const context={
  console,URL,Date:class extends Date{static now(){return now;}},
  location:{protocol:'http:',hostname:'127.0.0.1',href:'http://127.0.0.1:8790/index.html',hash:'#email-watcher'},
  document:{getElementById:id=>elements[id],querySelectorAll:selector=>selector.startsWith('#root')?[{id:'task-fixture'},{id:'installed-email-watcher'}]:[]},
  window:{scrollX:12,scrollY:940,scrollTo:(x,y)=>scroll.push([x,y])},
  setInterval:(fn,delay)=>timers.push([fn,delay]),
  fetch:async(url,options)=>{calls.push([String(url),options]);if(failed)throw new Error('fixture unavailable');return {ok:true,json:async()=>next};}
};
vm.createContext(context);vm.runInContext(SCRIPT,context);
assert.equal(elements.freshness.textContent,'collected 1 minutes ago');
assert.equal(timers.filter(t=>t[1]===60000).length,1);
(async()=>{
 await context.refresh();
 assert.equal(calls[0][0],'http://127.0.0.1:8790/status.json');
 assert.equal(calls[0][1].redirect,'error');assert.equal(calls[0][1].mode,'same-origin');
 assert.equal(calls[0][1].credentials,'omit');assert.equal(calls[0][1].cache,'no-store');
 assert.equal(context.location.hash,'#email-watcher');assert.deepEqual(scroll,[[12,940]]);assert.equal(detail.open,true);assert.equal(installedDetail.open,true);
 assert.match(elements.root.innerHTML,/id="installed-email-watcher"/);
 // Same rendered timestamp must not churn the DOM or move the scroll.
 await context.refresh();assert.equal(scroll.length,1);
 now=Date.parse('2026-10-13T12:45:00Z');context.freshness();
 assert.equal(elements.freshness.className,'banner');
 now+=1;context.freshness();assert.equal(elements.freshness.className,'banner bad');
 assert.match(elements.freshness.textContent,/collection stale.*collected 45 minutes ago/);
 failed=true;await context.refresh();
 assert.equal(elements.freshness.textContent,'dashboard service unreachable');assert.equal(elements.root.hidden,true);
 failed=false;await context.refresh();assert.equal(elements.root.hidden,false);
 // A non-loopback page never fetches; redirects are refused before a destination is contacted.
 context.location.hostname='example.invalid';const count=calls.length;await context.refresh();assert.equal(calls.length,count);
 context.location.hostname='localhost';context.location.protocol='file:';await context.refresh();assert.equal(calls.length,count);
})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('PAYLOAD', json.dumps(payload)).replace('SCRIPT', json.dumps(script))
    source = tmp_path / 'page.cjs';source.write_text(harness)
    node = shutil.which('node')
    assert node, 'Node is required to execute the fixture page behavior test'
    result = subprocess.run([node, str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    rendered = dashboard_html(payload, cat)
    assert 'location.reload' not in rendered and 'http-equiv="refresh"' not in rendered
    assert 'role="status" aria-live="polite"' in rendered
