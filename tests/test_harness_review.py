"""Review regressions: real fixture frameworks and fake owned runner steps."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import lcstatus.verify as verify
from lcstatus.outcomes import classify_execution
from test_verify_parsing import _pdf_runner, _run_pdf


def contracts_report(tests=2, failures=0, errors=0, skipped=0):
    return 'LCSTATUS_UNITTEST_RESULT ' + json.dumps(dict(
        schema_version=1, runner='accept_ew_ds', phase='contracts-test',
        tests=tests, failures=failures, errors=errors, skipped=skipped))


def test_pytest_failure_and_teardown_error_is_a_counted_failure(tmp_path):
    script = tmp_path / 'test_fixture.py'
    script.write_text('''import pytest
@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError('fixture teardown')
def test_failure(broken_teardown):
    assert False, 'fixture assertion'
def test_success():
    pass
''')
    report = tmp_path / 'report.xml'
    run = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(script),
                          '--junitxml=' + str(report)], cwd=tmp_path,
                         env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
                         capture_output=True, text=True)
    assert run.returncode == 1
    result = classify_execution('pytest', phase='test', exit_code=run.returncode, junit=report)
    assert (result['verdict'], result['executed'], result['failed']) == ('fail', 2, 1)
    assert result['detail']['junit_errors'] == 1


@pytest.mark.parametrize('code,report,verdict', [
    (1, contracts_report(failures=1), 'fail'),
    (1, contracts_report(errors=1), 'unavailable'),
    (1, '', 'unavailable'),
    (0, '', 'unavailable'),
    (-9, contracts_report(failures=1), 'unavailable'),
    (130, contracts_report(failures=1), 'unavailable'),
    (0, contracts_report(errors=1), 'unavailable'),
    (1, 'LCSTATUS_UNITTEST_RESULT {"schema_version":true}', 'unavailable'),
    (5, contracts_report(tests=0), 'unavailable'),
])
def test_contracts_test_step_requires_its_own_completed_counted_report(tmp_path, monkeypatch, code, report, verdict):
    runner, trees = _pdf_runner(tmp_path, monkeypatch)
    calls = []
    def step(cmd, **kw):
        calls.append(cmd)
        assert len(calls) == 1, 'a failed or unproved contracts step must stop later builds'
        return subprocess.CompletedProcess(cmd, code, report, 'fixture contracts diagnostic')
    monkeypatch.setattr(verify, 'run_owned_group', step)
    record = _run_pdf(runner, trees)
    assert record.verdict == verdict
    assert record.detail['execution_phase'] == 'contracts-test'
    assert record.exit_code == code and Path(record.log_path).is_file()
    assert not verify.decided('accept_ew_ds', record)
    if verdict == 'fail':
        assert (record.executed, record.failed) == (2, 1)
    else:
        assert record.detail['harness_reason']


@pytest.mark.parametrize('mode,code,verdict,failed,errors', [
    ('fail', 1, 'fail', 1, 0), ('error', 1, 'unavailable', 0, 1),
    ('double', 1, 'fail', 1, 1), ('subtests', 1, 'fail', 1, 0),
    ('unexpected', 1, 'fail', 1, 0), ('pass', 0, 'pass', 0, 0),
    ('skip', 0, 'pass', 0, 0),
])
def test_unittest_adapter_reports_native_result_without_product_edits(tmp_path, mode, code, verdict, failed, errors):
    from lcstatus.unittest_proof import bootstrap
    tests = tmp_path / 'tests'; tests.mkdir()
    script = tests / 'test_native.py'
    script.write_text('''import unittest, os
class Native(unittest.TestCase):
    def test_product(self):
        mode = os.environ['FIXTURE_MODE']
        if mode in ('fail', 'double'): self.fail('fixture assertion')
        if mode == 'error': raise RuntimeError('fixture harness error')
        if mode == 'skip': self.skipTest('fixture unavailable')
        if mode == 'subtests':
            for value in (1, 2):
                with self.subTest(value=value): self.assertEqual(value, 0)
    def tearDown(self):
        if os.environ['FIXTURE_MODE'] == 'double' and self._testMethodName == 'test_product':
            raise RuntimeError('fixture teardown')
    @unittest.expectedFailure
    def test_expected(self):
        if os.environ['FIXTURE_MODE'] != 'unexpected': self.fail('expected')
''')
    original = script.read_bytes()
    run = subprocess.run([sys.executable, '-c', bootstrap()], cwd=tmp_path,
                         env={**os.environ, 'FIXTURE_MODE':mode, 'PYTHONDONTWRITEBYTECODE':'1'},
                         capture_output=True, text=True)
    assert run.returncode == code and script.read_bytes() == original
    result = classify_execution('accept_ew_ds', phase='contracts-test', exit_code=run.returncode, stdout=run.stdout)
    assert (result['verdict'], result['failed'], result['detail']['unittest_errors']) == (verdict, failed, errors)
    assert 'Ran 2 tests' in run.stderr


def test_contracts_fetch_failure_keeps_diagnostic_and_stops_before_build(tmp_path, monkeypatch):
    runner, trees = _pdf_runner(tmp_path, monkeypatch)
    calls = []
    def fetch(cmd, **kwargs):
        calls.append(cmd)
        assert cmd[0] == '/tools/uv'
        return subprocess.CompletedProcess(cmd, 1, '', 'uv fetch failed: fixture network unavailable')
    monkeypatch.setattr(verify, 'run_owned_group', fetch)
    record = _run_pdf(runner, trees)
    assert len(calls) == 1 and record.verdict == 'unavailable'
    assert record.detail['harness_reason'] and 'uv fetch failed' in Path(record.log_path).read_text()
    assert record.failed is None


@pytest.mark.parametrize('change', [dict(tests=True), dict(failures=-1), dict(errors=3),
                                  dict(skipped=2, failures=1), dict(runner='pytest'),
                                  dict(phase='prepare'), dict(extra='unexpected')])
def test_unittest_report_rejects_invalid_counts_and_schema(change):
    report = json.loads(contracts_report().split(' ', 1)[1]);report.update(change)
    output = 'LCSTATUS_UNITTEST_RESULT ' + json.dumps(report)
    result = classify_execution('accept_ew_ds', phase='contracts-test', exit_code=1, stdout=output)
    assert result['verdict'] == 'unavailable' and result['failed'] is None


def test_unittest_receipt_must_be_single_terminal_and_only_in_contracts_phase():
    report = contracts_report(failures=1)
    for output in (report + '\n' + report, report + '\nafter report',
                   'LCSTATUS_UNITTEST_RESULT {"schema_version":1,"schema_version":1}'):
        assert classify_execution('accept_ew_ds', phase='contracts-test', exit_code=1, stdout=output)['verdict'] == 'unavailable'
    for phase in ('prepare', 'test'):
        assert classify_execution('accept_ew_ds', phase=phase, exit_code=1, stdout=report)['verdict'] == 'unavailable'


@pytest.mark.parametrize('tests,skipped', [(2, 0), (2, 2), (0, 0)])
def test_valid_contracts_completion_is_consumed_by_owner_and_continues(tmp_path, monkeypatch, tests, skipped):
    runner, trees = _pdf_runner(tmp_path, monkeypatch)
    calls = []
    owner_calls = []
    def owner(name, **kwargs):
        owner_calls.append((name, kwargs['phase']))
        return classify_execution(name, **kwargs)
    def step(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0,
            contracts_report(tests=tests, skipped=skipped) if cmd[0] == '/tools/uv' else '', '')
    monkeypatch.setattr(verify, 'classify_execution', owner)
    monkeypatch.setattr(verify, 'run_owned_group', step)
    result = _run_pdf(runner, trees)
    assert result.verdict == 'pass' and len(calls) == 4
    assert result.detail['contracts_unittest'] == dict(schema_version=1, runner='accept_ew_ds',
        phase='contracts-test', tests=tests, failures=0, errors=0, skipped=skipped)
    assert owner_calls == [('accept_ew_ds', 'contracts-test'), ('accept_ew_ds', 'test')]


def test_harness_reason_identity_changes_only_for_a_changed_stable_fault(tmp_path):
    from lcstatus.evidence import Record, Store
    store = Store(tmp_path / 'records.jsonl')
    def observation(reason, **metadata):
        outcome = classify_execution('pytest', reason=reason)
        return Record(kind='automated_test', repo='fixture', revision='a'*40,
                      **outcome, **metadata)
    first = observation('missing entitlement', log_path='first.log')
    assert store.add(first)
    prefix = store.path.read_bytes()
    assert not store.add(observation('missing entitlement', log_path='second.log', duration_s=17))
    changed = observation('expired entitlement')
    assert first.series_identity() == changed.series_identity()
    assert store.add(changed) and store.path.read_bytes().startswith(prefix)
    assert [r.detail['harness_reason'] for r in store.all()] == ['missing entitlement', 'expired entitlement']
    for verdict in ('fail', 'pass'):
        a = Record(kind='automated_test', repo='fixture', revision='a'*40, verdict=verdict, detail={'harness_reason':'one'})
        b = Record(kind='automated_test', repo='fixture', revision='a'*40, verdict=verdict, detail={'harness_reason':'two'})
        assert a.identity() == b.identity()
    old_a = Record(kind='automated_test', repo='fixture', revision='a'*40, verdict='unavailable', summary='one')
    old_b = Record(kind='automated_test', repo='fixture', revision='a'*40, verdict='unavailable', summary='two')
    assert old_a.identity() == old_b.identity()


def test_variable_tokens_cannot_make_the_same_harness_fault_new(tmp_path):
    from lcstatus.evidence import Record, Store
    store = Store(tmp_path / 'records.jsonl')
    records = []
    for reason in ('fetch fault pid=1234 at 2026-10-14T20:00:00Z elapsed=0.2s',
                   'fetch fault pid=9876 at 2026-10-14T20:01:00Z elapsed=2.1s'):
        records.append(Record(kind='automated_test', repo='fixture', revision='a'*40,
                              **classify_execution('pytest', reason=reason)))
    assert records[0].detail['harness_reason'] == records[1].detail['harness_reason']
    assert '1234' not in records[0].summary and '2026-10-14' not in records[0].summary
    assert store.add(records[0]) and not store.add(records[1])


def test_environment_stderr_is_diagnostic_not_a_variable_fault_reason(tmp_path, monkeypatch):
    from lcstatus.sources import Revision
    tree = tmp_path / 'tree';tree.mkdir()
    class Mirrors:
        def extract(self, *args): return tree
    runner = verify.Runner(Mirrors(), tmp_path/'cache', tmp_path/'logs', object(), {'repos':{'fixture':{}}})
    monkeypatch.setattr(verify.shutil, 'which', lambda name: '/tools/' + name)
    records = []
    for stderr in ('uv fetch fault pid=1111 elapsed=0.2s', 'uv fetch fault pid=2222 elapsed=0.8s'):
        monkeypatch.setattr(verify.subprocess, 'run', lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, '', stderr))
        records.append(runner.pytest('fixture', {'repo':'fixture'}, Revision('fixture','a'*40,'2026-10-14T00:00:00Z',''), [], []))
        assert records[-1].detail['setup_stderr'] == stderr
    assert records[0].detail['harness_reason'] == records[1].detail['harness_reason']
    assert records[0].identity() == records[1].identity()


@pytest.mark.parametrize('mode', ['pass', 'fail', 'error', 'empty'])
def test_unittest_adapter_preserves_native_exit_for_every_completion(tmp_path, mode):
    from lcstatus.unittest_proof import bootstrap
    tests = tmp_path / 'tests';tests.mkdir()
    if mode != 'empty':
        (tests/'test_native.py').write_text('''import unittest, os
class Native(unittest.TestCase):
    def test_result(self):
        if os.environ['FIXTURE_MODE'] == 'fail': self.fail('fixture assertion')
        if os.environ['FIXTURE_MODE'] == 'error': raise RuntimeError('fixture harness fault')
''')
    env = dict(os.environ, FIXTURE_MODE=mode, PYTHONDONTWRITEBYTECODE='1')
    direct = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    adapted = subprocess.run([sys.executable, '-c', bootstrap()],
                             cwd=tmp_path, env=env, capture_output=True, text=True)
    assert adapted.returncode == direct.returncode
    report = json.loads(adapted.stdout.split('LCSTATUS_UNITTEST_RESULT ', 1)[1])
    assert report['tests'] == (0 if mode=='empty' else 1)
    expected = 'unavailable' if mode=='error' or direct.returncode==5 else ('fail' if mode=='fail' else ('skip' if mode=='empty' else 'pass'))
    assert classify_execution('accept_ew_ds', phase='contracts-test', exit_code=adapted.returncode, stdout=adapted.stdout)['verdict'] == expected

