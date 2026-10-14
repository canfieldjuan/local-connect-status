"""Contract 12: synthetic runners, exact-tree fixtures and isolated subprocesses only."""
import json
import subprocess
import sys
import os
import venv
from pathlib import Path

import pytest

from lcstatus.sources import Failure, Revision
from lcstatus.verify import Runner, decided
import lcstatus.verify as verify
from test_verify_parsing import (
    _accept, _cross_app_runner, _install_host_entitlement, _pdf_runner, _run_pdf,
)


def pytest_runner(tmp_path, monkeypatch, code, xml=None):
    tree = tmp_path / 'tree';tree.mkdir()
    class Mirrors:
        def extract(self, *args): return tree
    runner = Runner(Mirrors(), tmp_path / 'cache', tmp_path / 'logs', object(), {'repos': {'fixture': {}}})
    monkeypatch.setattr(runner, '_venv', lambda *args: tree / '.venv')
    monkeypatch.setattr(verify, 'interpreter_version', lambda *args: 'fixture Python')
    def completed(cmd, **kwargs):
        if xml is not None:
            Path(next(x.split('=', 1)[1] for x in cmd if x.startswith('--junitxml='))).write_text(xml)
        return subprocess.CompletedProcess(cmd, code, 'passing-looking prose', 'fixture diagnostic')
    monkeypatch.setattr(verify.subprocess, 'run', completed)
    return runner.pytest('fixture.pytest', {'repo': 'fixture'}, Revision('fixture', 'a'*40, '2026-10-14T00:00:00Z', ''), [], [])


def assert_harness(record, runner):
    assert record.verdict == 'unavailable'
    assert record.detail['harness_reason']
    assert record.summary == 'harness fault: ' + record.detail['harness_reason']
    assert not decided(runner, record)


@pytest.mark.parametrize('code,xml', [
    (-9, None), (4, None), (5, '<testsuite tests="0" failures="0" errors="0" skipped="0"/>'),
    (2, '<testsuite tests="1" failures="0" errors="1" skipped="0"/>'),
])
def test_harness_pytest_abnormal_exits_are_unavailable(tmp_path, monkeypatch, code, xml):
    record = pytest_runner(tmp_path, monkeypatch, code, xml)
    assert_harness(record, 'pytest')
    assert record.exit_code == code


def test_harness_pytest_counted_failure_stays_fail(tmp_path, monkeypatch):
    record = pytest_runner(tmp_path, monkeypatch, 1, '<testsuite tests="2" failures="1" errors="0" skipped="0"/>')
    assert record.verdict == 'fail' and record.failed == 1 and record.executed == 2
    assert decided('pytest', record)


@pytest.mark.parametrize('phase,code,output', [
    (0, 1, 'npm fetch failed'), (1, 1, 'build failed'),
    (2, 101, 'crates fetch failed'), (2, -9, 'rustc killed'),
    (0, 1, 'test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured'),
])
def test_harness_cargo_preparation_or_uncounted_exit_is_unavailable(tmp_path, monkeypatch, phase, code, output):
    tree = tmp_path / 'tree';tree.mkdir()
    class Mirrors:
        def extract(self, *args): return tree
    runner = Runner(Mirrors(), tmp_path / 'cache', tmp_path / 'logs', object(), {'repos': {'fixture': {}}})
    monkeypatch.setattr(verify.shutil, 'which', lambda name: '/tools/' + name)
    calls = []
    def step(cmd, **kwargs):
        index = len(calls);calls.append(cmd)
        return subprocess.CompletedProcess(cmd, code if index == phase else 0, output if index == phase else '', '')
    monkeypatch.setattr(verify, 'run_owned_group', step)
    record = runner.cargo_lib('fixture.cargo', {'repo':'fixture'}, Revision('fixture','a'*40,'2026-10-14T00:00:00Z',''), [], [])
    assert_harness(record, 'cargo_lib')
    assert record.exit_code == code and record.failed is None


@pytest.mark.parametrize('runner_name', ['accept_ew_ip', 'accept_ew_ds'])
def test_harness_acceptance_nonzero_without_proof_is_unavailable(tmp_path, monkeypatch, runner_name):
    if runner_name == 'accept_ew_ip':
        runner, trees, revs = _cross_app_runner(tmp_path, monkeypatch)
        _install_host_entitlement(monkeypatch, tmp_path / 'home')
        monkeypatch.setattr(verify.subprocess, 'run', lambda cmd, **kw: subprocess.CompletedProcess(cmd,1,'STOP product-looking prose',''))
        record = _accept(runner, trees, revs)
    else:
        runner, trees = _pdf_runner(tmp_path, monkeypatch)
        def step(cmd, **kw):
            proof = cmd[0] == '/tools/xvfb-run'
            return subprocess.CompletedProcess(cmd,1 if proof else 0,'','Xvfb failed' if proof else '')
        monkeypatch.setattr(verify, 'run_owned_group', step)
        record = _run_pdf(runner, trees)
    assert_harness(record, runner_name)
    assert record.failed is None


def test_harness_unstaged_licence_is_unavailable_with_reason(tmp_path, monkeypatch):
    runner, trees, revs = _cross_app_runner(tmp_path, monkeypatch)
    _install_host_entitlement(monkeypatch, tmp_path / 'home')
    monkeypatch.setattr(verify, 'stage_entitlement', lambda *a: (_ for _ in ()).throw(OSError('fixture staging fault')))
    monkeypatch.setattr(verify.subprocess, 'run', lambda *a, **k: pytest.fail('unstaged licence launched product'))
    assert_harness(_accept(runner, trees, revs), 'accept_ew_ip')


def test_harness_uv_fetch_failure_is_unavailable_with_reason(tmp_path, monkeypatch):
    runner, trees = _pdf_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(verify, 'run_owned_group', lambda cmd, **kw: subprocess.CompletedProcess(cmd,1,'','uv fetch failed'))
    assert_harness(_run_pdf(runner, trees), 'accept_ew_ds')


@pytest.mark.parametrize('runner_name', ['accept_ew_ip', 'accept_ew_ds'])
def test_acceptance_adapter_success_has_direct_main_semantics_and_same_venv(tmp_path, runner_name):
    from lcstatus.acceptance_proof import bootstrap
    fixture_venv = tmp_path / 'venv';venv.EnvBuilder(with_pip=False).create(fixture_venv)
    python = str(fixture_venv / 'bin/python')
    scripts = tmp_path / 'scripts';scripts.mkdir()
    (scripts / 'sibling.py').write_text('VALUE = 37\n')
    script = scripts / 'product.py'
    script.write_text('''import json, os, sys, __main__, sibling, atexit
from pathlib import Path
def require(checks, name, condition):
    if condition is not True: raise RuntimeError(name)
    checks[name] = True
def require_proof_checks(checks):
    if False in checks.values(): raise RuntimeError('product failure')
if __name__ == '__main__':
    assert __main__.__dict__ is globals()
    require({}, 'ledger_one_entry', True)
    require_proof_checks({'job_completed':True})
    value = dict(name=__name__, file=__file__, package=__package__, spec=__spec__,
                 loader=type(__loader__).__name__, cached=__cached__, builtins=type(__builtins__).__name__,
                 executable=sys.executable, prefix=sys.prefix, argv=sys.argv, path=sys.path,
                 home=os.environ['HOME'], fixture=os.environ['FIXTURE_VALUE'], sibling=sibling.VALUE)
    Path('product-side-effect').write_text(json.dumps(value,sort_keys=True))
    print(json.dumps(value,sort_keys=True))
    def final_observation():
        import __main__
        assert __main__.__dict__ is globals()
        print('shutdown', getattr(__main__, '__file__', None), sys.argv)
        Path('shutdown-side-effect').write_text(repr(getattr(__main__, '__file__', None)) + repr(sys.argv))
    atexit.register(final_observation)
''')
    env = dict(os.environ, HOME=str(tmp_path/'home'), FIXTURE_VALUE='same', PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
    direct = subprocess.run([python,str(script),'same-argument'],cwd=tmp_path,env=env,capture_output=True,text=True)
    effect = (tmp_path/'product-side-effect').read_bytes();(tmp_path/'product-side-effect').unlink()
    shutdown = (tmp_path/'shutdown-side-effect').read_bytes();(tmp_path/'shutdown-side-effect').unlink()
    adapted = subprocess.run([python,'-c',bootstrap(script,runner_name),'same-argument'],cwd=tmp_path,env=env,capture_output=True,text=True)
    assert (direct.returncode,direct.stdout,direct.stderr) == (adapted.returncode,adapted.stdout,adapted.stderr)
    assert adapted.returncode == 0
    assert (tmp_path/'product-side-effect').read_bytes() == effect
    assert (tmp_path/'shutdown-side-effect').read_bytes() == shutdown
    assert json.loads(adapted.stdout.splitlines()[0])['prefix'] == str(fixture_venv)


@pytest.mark.parametrize('runner_name', ['accept_ew_ip', 'accept_ew_ds'])
@pytest.mark.parametrize('mode', ['product', 'arbitrary', 'cleanup', 'licence', 'caught'])
def test_acceptance_adapter_witnesses_only_propagated_native_product_assertion(tmp_path, runner_name, mode):
    from lcstatus.acceptance_proof import bootstrap
    from lcstatus.outcomes import classify_execution, ACCEPTANCE_PREFIX
    script = tmp_path/'product.py'
    script.write_text('''import sys
def require(checks,name,condition):
    if condition is not True: raise RuntimeError('native assertion')
def require_proof_checks(checks):
    if False in checks.values(): raise RuntimeError('native assertion')
if __name__ == '__main__':
    mode,runner = sys.argv[1:]
    if mode=='arbitrary': raise RuntimeError('product-looking text is not proof')
    if mode=='licence':
        require({},'entitlement_active',False)
    try:
        if runner=='accept_ew_ip': require({},'ledger_one_entry',False)
        else: require_proof_checks({'job_completed':False,'provider_available':True})
    except RuntimeError:
        if mode!='caught': raise
    finally:
        if mode=='cleanup': raise OSError('fixture cleanup failure')
''')
    run = subprocess.run([sys.executable,'-c',bootstrap(script,runner_name),mode,runner_name],
                         cwd=tmp_path,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
    outcome = classify_execution(runner_name,phase='test',exit_code=run.returncode,stdout=run.stdout)
    if mode=='product':
        assert outcome['verdict']=='fail' and outcome['failed']==1
        assert run.stdout.count(ACCEPTANCE_PREFIX)==1
        from lcstatus.evidence import Record
        assert not decided(runner_name,Record(kind='automated_test',repo='fixture',revision='a'*40,**outcome))
    elif mode=='caught' and runner_name=='accept_ew_ds':
        assert run.returncode==0 and outcome['verdict']=='pass'
        assert ACCEPTANCE_PREFIX not in run.stdout
    else:
        assert outcome['verdict']=='unavailable'
        assert ACCEPTANCE_PREFIX not in run.stdout


@pytest.mark.parametrize('code,report', [(1,None),(1,'broken'),(1,'<testsuite tests="-1" failures="1"/>'),
                                       (1,'<testsuite tests="1" failures="0" errors="1"/>')])
def test_unproved_pytest_failures_cannot_be_fail(tmp_path, monkeypatch, code, report):
    assert_harness(pytest_runner(tmp_path,monkeypatch,code,report),'pytest')


def test_acceptance_receipts_fail_closed_and_counted_cargo_failure_is_positive():
    from lcstatus.outcomes import ACCEPTANCE_PREFIX, classify_execution
    good=dict(schema_version=1,type='product_failure',runner='accept_ew_ip',checks={'ledger_one_entry':False},failed_checks=['ledger_one_entry'])
    text=ACCEPTANCE_PREFIX+json.dumps(good)
    assert classify_execution('accept_ew_ip',phase='test',exit_code=1,stdout=text)['verdict']=='fail'
    for bad in [dict(good,runner='accept_ew_ds'),dict(good,schema_version=True),dict(good,checks={'ledger_one_entry':0}),
                dict(good,failed_checks=[]),dict(good,checks={'entitlement_active':False},failed_checks=['entitlement_active'])]:
        assert classify_execution('accept_ew_ip',phase='test',exit_code=1,stdout=ACCEPTANCE_PREFIX+json.dumps(bad))['verdict']=='unavailable'
    for output in [text+'\n'+text,text+'\ncleanup output',ACCEPTANCE_PREFIX+'{"schema_version":1,"schema_version":1}']:
        assert classify_execution('accept_ew_ip',phase='test',exit_code=1,stdout=output)['verdict']=='unavailable'
    assert classify_execution('accept_ew_ip',phase='prepare',exit_code=1,stdout=text)['verdict']=='unavailable'
    cargo=classify_execution('cargo_lib',phase='test',exit_code=101,stdout='test result: FAILED. 2 passed; 1 failed; 0 ignored; 0 measured; finished in 0s')
    assert (cargo['verdict'],cargo['executed'],cargo['failed'])==('fail',3,1)
    for code in (-9,130,137):
        assert classify_execution('cargo_lib',phase='test',exit_code=code,
            stdout='test result: FAILED. 2 passed; 1 failed; 0 ignored; 0 measured')['verdict']=='unavailable'


def test_cargo_failure_counts_across_the_actual_test_phase_reports():
    from lcstatus.outcomes import classify_execution
    output=('test result: FAILED. 2 passed; 1 failed; 0 ignored; 0 measured\n'
            'test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured\n')
    result=classify_execution('cargo_lib',phase='test',exit_code=101,stdout=output)
    assert (result['verdict'],result['executed'],result['failed'])==('fail',5,1)


@pytest.mark.parametrize('runner_name', ['pytest','cargo_lib','accept_ew_ip','accept_ew_ds'])
def test_every_executing_runner_consumes_the_owner_for_prerequisite_failure(tmp_path, monkeypatch, runner_name):
    from lcstatus.outcomes import classify_execution
    calls=[]
    def owner(name,**kwargs):
        calls.append((name,kwargs));return classify_execution(name,**kwargs)
    monkeypatch.setattr(verify,'classify_execution',owner)
    if runner_name in ('pytest','cargo_lib'):
        class Mirrors:
            def extract(self,*args):return Failure('fixture','tree unavailable')
        runner=Runner(Mirrors(),tmp_path/'cache',tmp_path/'logs',object(),{'repos':{'fixture':{}}})
        method=getattr(runner,runner_name)
        record=method('fixture.check',{'repo':'fixture'},Revision('fixture','a'*40,'2026-10-14T00:00:00Z',''),[],[])
    elif runner_name=='accept_ew_ip':
        runner,trees,revs=_cross_app_runner(tmp_path,monkeypatch)
        monkeypatch.setenv('HOME',str(tmp_path/'missing-home'));monkeypatch.delenv('XDG_CONFIG_HOME',raising=False)
        record=_accept(runner,trees,revs)
    else:
        runner,trees=_pdf_runner(tmp_path,monkeypatch)
        (trees['connect-contracts']/'requirements-dev.txt').unlink()
        record=_run_pdf(runner,trees)
    assert [name for name,kw in calls]==[runner_name]
    assert calls[0][1]['reason'] and calls[0][1]['phase']=='prepare'
    assert_harness(record,runner_name)


@pytest.mark.parametrize('runner_name', ['pytest','cargo_lib','accept_ew_ip','accept_ew_ds'])
def test_every_executing_runner_consumes_the_owner_for_test_completion(tmp_path, monkeypatch, runner_name):
    from lcstatus.outcomes import classify_execution
    from test_verify_parsing import _complete_invoice_proof
    calls=[]
    def owner(name,**kwargs):
        calls.append((name,kwargs));result=classify_execution(name,**kwargs)
        assert result['verdict']=='pass'
        result['verdict']='skip';result['detail']['fixture_owner_marker']=True
        return result
    monkeypatch.setattr(verify,'classify_execution',owner)
    if runner_name=='pytest':
        record=pytest_runner(tmp_path,monkeypatch,0,'<testsuite tests="1" failures="0" errors="0" skipped="0"/>')
    elif runner_name=='cargo_lib':
        tree=tmp_path/'tree';tree.mkdir()
        class Mirrors:
            def extract(self,*args):return tree
        runner=Runner(Mirrors(),tmp_path/'cache',tmp_path/'logs',object(),{'repos':{'fixture':{}}})
        monkeypatch.setattr(verify.shutil,'which',lambda name:'/tools/'+name)
        monkeypatch.setattr(verify,'run_owned_group',lambda cmd,**kw:subprocess.CompletedProcess(cmd,0,
            'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured' if cmd[0]=='cargo' else '', ''))
        record=runner.cargo_lib('fixture',{'repo':'fixture'},Revision('fixture','a'*40,'2026-10-14T00:00:00Z',''),[],[])
    elif runner_name=='accept_ew_ip':
        runner,trees,revs=_cross_app_runner(tmp_path,monkeypatch)
        _install_host_entitlement(monkeypatch,tmp_path/'home')
        monkeypatch.setattr(verify.subprocess,'run',lambda cmd,**kw:subprocess.CompletedProcess(cmd,0,_complete_invoice_proof(),''))
        record=_accept(runner,trees,revs)
    else:
        runner,trees=_pdf_runner(tmp_path,monkeypatch)
        monkeypatch.setattr(verify,'run_owned_group',lambda cmd,**kw:subprocess.CompletedProcess(cmd,0,'',''))
        record=_run_pdf(runner,trees)
    assert [(name,kw['phase']) for name,kw in calls]==[(runner_name,'test')]
    assert record.verdict=='skip' and record.detail['fixture_owner_marker'] is True


def test_new_harness_row_preserves_history_and_renders_one_rules_owned_label(tmp_path):
    from lcstatus.evidence import Record,Store,check_fingerprint,condition_fingerprint
    from lcstatus.rules import condition_status,TaskStatus
    from lcstatus.render import status_payload,report_md,dashboard_html
    condition=dict(id='fixture.condition',kind='automated_test',check='fixture.check',proves='Fixture behavior')
    check=dict(repo='fixture',runner='pytest')
    source=dict(type='local_runner',check='fixture.check',check_fingerprint=check_fingerprint(check),
                condition_fingerprints={'fixture.condition':condition_fingerprint(condition)})
    store=Store(tmp_path/'data')
    old=Record(kind='automated_test',repo='fixture',revision='a'*40,verdict='fail',exit_code=-9,
               condition_ids=['fixture.condition'],source=source,recorded_at='2026-10-14T01:00:00Z')
    store.add(old);prefix=store.path.read_bytes()
    reason='SIGKILL <script> | fixture'
    new=Record(kind='automated_test',repo='fixture',revision='a'*40,verdict='unavailable',exit_code=-9,
               condition_ids=['fixture.condition'],source=source,recorded_at='2026-10-14T02:00:00Z',
               summary='harness fault: '+reason,detail={'harness_reason':reason})
    store.add(new);assert store.path.read_bytes().startswith(prefix)
    records=store.all();assert records[0].verdict=='fail'
    before=condition_status(condition,[records[0]],{'fixture':'a'*40},'fixture',check)
    after=condition_status(condition,records,{'fixture':'a'*40},'fixture',check)
    assert before.state=='check_failed' and before.harness_label() is None
    assert after.state=='not_checked' and after.current.verdict=='unavailable'
    task=TaskStatus(dict(id='fixture',app='fixture',layer='standalone',title='Fixture',promise='Fixture behavior'),
                    'planned','not_checked',[after],{},[])
    cat={'release':{'target':'fixture installers','automate_scope':{'decision':'undecided','note':'fixture'}}}
    p=status_payload(cat,[task],{'fixture':'a'*40},[],{'runs':1}, {}, records)
    label=p['tasks'][0]['conditions'][0]['label']
    assert label=='check unavailable: harness fault ('+reason+')'
    assert 'check failed' not in label
    md=report_md(p,cat);html=dashboard_html(p,cat)
    assert 'check unavailable: harness fault' in md and 'check unavailable: harness fault' in html
    assert '<script> | fixture' not in html and '\\u003cscript\\u003e' in html
    assert '\\|' in md
