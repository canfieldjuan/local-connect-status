"""Malformed pin receipts refuse at the shared release owner, using only fixtures."""
import json
import os

import pytest

from lcstatus.deploy import Deployer
from lcstatus.retention import Retention
from test_retention import A, B, C, D, E, layout, owner, obsolete
from test_self_deploy import world, bootstrap, commit, attempts

SHAPES = [[], 'not an object', 42, None]


@pytest.mark.parametrize('shape', SHAPES, ids=['array', 'string', 'number', 'null'])
@pytest.mark.parametrize('pin', ['current', 'previous'])
def test_invalid_pin_receipt_shape_refuses_with_audit_and_no_deletion(layout, shape, pin):
    paths, deployer, _ = layout
    sha = A if pin == 'current' else B
    receipt = paths.releases / sha / '.release.json'
    receipt.write_text(json.dumps(shape))
    original_records = deployer.store.path.read_bytes()
    pins = {name: os.readlink(paths.root / name) for name in ('current', 'previous')}
    result = owner(deployer).run(apply=True)
    assert result['outcome'] == 'refused' and result['deleted'] == [] and result['errors']
    assert all((paths.releases / value).is_dir() for value in (A, B, C, D, E))
    assert deployer.store.path.read_bytes() == original_records
    assert {name: os.readlink(paths.root / name) for name in pins} == pins
    rows = [json.loads(line) for line in open(result['journal'])]
    assert [row['event'] for row in rows] == ['start', 'error', 'finished']
    assert rows[-1]['outcome'] == 'refused'


@pytest.mark.parametrize('shape', SHAPES, ids=['array', 'string', 'number', 'null'])
def test_switch_refuses_invalid_shape_at_release_owner_without_changing_pins(layout, shape):
    paths, deployer, _ = layout
    (paths.releases / C / '.release.json').write_text(json.dumps(shape))
    with pytest.raises(ValueError, match='passing exact-revision suite receipt'):
        deployer.switch('current', C)
    assert deployer.pin('current') == A and deployer.pin('previous') == B


@pytest.mark.parametrize('shape', SHAPES, ids=['array', 'string', 'number', 'null'])
@pytest.mark.parametrize('pin', ['current', 'previous'])
def test_recovery_refuses_invalid_shape_and_keeps_pending_context(layout, shape, pin):
    paths, deployer, _ = layout
    sha = A if pin == 'current' else B
    (paths.releases / sha / '.release.json').write_text(json.dumps(shape))
    context = deployer.context(A, A, B)
    deployer.pending.write_text(json.dumps(context))
    pending = deployer.pending.read_bytes()
    records = deployer.store.path.read_bytes()
    pins = {name: os.readlink(paths.root / name) for name in ('current', 'previous')}
    with pytest.raises(ValueError, match='passing exact-revision suite receipt'):
        deployer.recover()
    assert deployer.pending.read_bytes() == pending
    assert deployer.store.path.read_bytes() == records
    assert {name: os.readlink(paths.root / name) for name in pins} == pins


@pytest.mark.parametrize('shape', SHAPES, ids=['array', 'string', 'number', 'null'])
@pytest.mark.parametrize('pin', ['current', 'previous'])
def test_late_invalid_receipt_cannot_change_a_completed_tick(world, monkeypatch, shape, pin):
    work, paths, deployer, command = world
    previous = bootstrap(world)
    current = commit(work)
    obsolete_tree = obsolete(paths)
    run = Retention.run
    calls = []
    def corrupt_before_cleanup(self, **kwargs):
        assert not deployer.pending.exists()
        assert attempts(paths)[-1].detail['outcome'] == 'deployed'
        sha = current if pin == 'current' else previous
        (paths.releases / sha / '.release.json').write_text(json.dumps(shape))
        result = run(self, **kwargs)
        calls.append(result)
        return result
    monkeypatch.setattr(Retention, 'run', corrupt_before_cleanup)
    assert deployer.tick(collect=True) == 0
    assert len(calls) == 1 and calls[0]['outcome'] == 'refused' and calls[0]['deleted'] == []
    assert obsolete_tree.is_dir()
    assert paths.current.resolve().name == current and paths.previous.resolve().name == previous
    assert [row.detail['outcome'] for row in attempts(paths)] == ['deployed', 'deployed']
    page = json.loads((paths.site / 'status.json').read_text())['dashboard_code']
    assert page['color'] == 'green' and page['deployed'] == current
    rows = [json.loads(line) for line in open(calls[0]['journal'])]
    assert rows[-1]['event'] == 'finished' and rows[-1]['outcome'] == 'refused'


def test_retention_consumes_shared_release_receipt_owner_without_requiring_old_environment(layout, monkeypatch):
    paths, deployer, _ = layout
    (paths.releases / C / '.venv/bin/python').unlink()
    calls = []
    release = Deployer.release
    def observed(self, sha, **kwargs):
        calls.append((sha, kwargs))
        return release(self, sha, **kwargs)
    monkeypatch.setattr(Deployer, 'release', observed)
    result = owner(deployer).run(apply=True)
    assert result['deleted'] == [C, D, E]
    assert any(sha == C and options.get('receipt_only') is True and options.get('receipt_fd') is not None
               for sha, options in calls)
    assert deployer.pin('current') == A and deployer.pin('previous') == B


def test_pins_and_switch_still_require_the_kept_environment(layout):
    paths, deployer, _ = layout
    (paths.releases / C / '.venv/bin/python').unlink()
    with pytest.raises(ValueError, match='environment is missing'):
        deployer.switch('current', C)
    assert deployer.pin('current') == A and deployer.pin('previous') == B
