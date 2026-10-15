"""Contract 13: temporary release roots only; no live data or installed programs."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lcstatus.deploy import Deployer
from lcstatus.evidence import Record, collection_lock
from lcstatus.paths import Paths
from test_self_deploy import world, bootstrap, commit, attempts

A, B, C, D, E = (letter * 40 for letter in 'abcde')


@pytest.fixture
def layout(tmp_path):
    paths = Paths.configured(tmp_path / 'runtime')
    paths.releases.mkdir(parents=True)
    lock = collection_lock(paths.data, wait=False)
    deployer = Deployer(paths, lock)
    def release(sha, receipt=True):
        tree = paths.releases / sha
        (tree / '.venv/bin').mkdir(parents=True)
        (tree / '.venv/bin/python').write_text('fixture interpreter, never executed')
        (tree / 'program').write_text('fixture code')
        if receipt:
            (tree / '.release.json').write_text(json.dumps({'revision':sha, 'tests':'pass'}))
        return tree
    for sha in (A, B, C, D, E): release(sha)
    deployer.switch('current', A);deployer.switch('previous', B)
    for directory in (paths.cache, paths.site, paths.data / 'logs'):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'existing-sentinel').write_bytes(b'preserve existing bytes')
    deployer.store.add(Record(kind='deployment',repo='local-connect-status',revision=A,verdict='pass',
        source={'type':'dashboard_deploy','attempt':'fixture'},detail={'outcome':'deployed'}))
    yield paths, deployer, release
    lock.close()


def owner(deployer, **kwargs):
    from lcstatus.retention import Retention
    return Retention(deployer, **kwargs)


def test_preview_apply_preserve_pins_controller_and_all_runtime_evidence(layout):
    paths, deployer, _ = layout
    prefix = deployer.store.path.read_bytes()
    preview = owner(deployer, executing=paths.releases / C).run()
    assert preview['outcome'] == 'planned' and preview['candidates'] == [D, E]
    assert preview['protected'] == [A, B, C]
    assert not list((paths.data / 'logs').glob('retention-*'))
    assert all((paths.releases / sha).is_dir() for sha in (A,B,C,D,E))
    applied = owner(deployer, executing=paths.releases / C).run(apply=True)
    assert applied['outcome'] == 'applied' and applied['deleted'] == [D,E]
    assert all((paths.releases / sha).is_dir() for sha in (A,B,C))
    assert deployer.pin('current') == A and deployer.pin('previous') == B
    assert deployer.store.path.read_bytes() == prefix
    for directory in (paths.cache, paths.site, paths.data / 'logs'):
        assert (directory / 'existing-sentinel').read_bytes() == b'preserve existing bytes'
    rows = [json.loads(line) for line in Path(applied['journal']).read_text().splitlines()]
    assert rows[0]['event'] == 'start' and rows[0]['candidates'] == [D,E]
    assert [r['revision'] for r in rows if r['event']=='deleted'] == [D,E]
    assert rows[-1]['event'] == 'finished' and rows[-1]['deleted'] == [D,E]
    assert owner(deployer, executing=paths.releases/C).run(apply=True)['deleted'] == []


@pytest.mark.parametrize('journal', ['{}','not json','broken-link'])
def test_any_pending_journal_blocks_every_deletion(layout, journal):
    paths, deployer, _ = layout
    pending = paths.data / 'deployment-pending.json'
    if journal=='broken-link': pending.symlink_to(paths.data/'missing')
    else: pending.write_text(journal)
    result = owner(deployer).run(apply=True)
    assert result['outcome']=='refused' and result['deleted']==[]
    assert all((paths.releases/sha).exists() for sha in (A,B,C,D,E))
    assert os.path.lexists(pending)
    if journal!='broken-link': assert pending.read_text()==journal


def test_owned_failed_tree_requires_terminal_record_and_names_alone_are_not_proof(layout):
    paths, deployer, _ = layout
    for sha in (C,D): (paths.releases/sha/'.release.json').unlink()
    deployer.store.add(Record(kind='deployment',repo='local-connect-status',revision=C,verdict='fail',
        source={'type':'dashboard_deploy','attempt':'fixture-failure'},detail={'outcome':'tests-failed'}))
    (paths.releases/'operator-notes').mkdir()
    result=owner(deployer).run(apply=True)
    assert result['deleted']==[C,E] and result['skipped'][D]
    assert (paths.releases/D).is_dir() and (paths.releases/'operator-notes').is_dir()


@pytest.mark.parametrize('fault', ['current','previous','receipt','top-link','root-link'])
def test_ambiguous_control_or_ownership_refuses_before_any_deletion(layout,tmp_path,fault):
    paths,deployer,_=layout
    outside=tmp_path/'outside';outside.mkdir();(outside/'sentinel').write_bytes(b'outside')
    if fault in ('current','previous'):
        link=paths.root/fault;link.unlink();link.symlink_to(outside)
    elif fault=='receipt':
        (paths.releases/C/'.release.json').write_text(json.dumps({'revision':D,'tests':'pass'}))
    elif fault=='top-link':
        shutil.rmtree(paths.releases/C);(paths.releases/C).symlink_to(outside)
    else:
        paths.releases.rename(paths.root/'kept-releases');paths.releases.symlink_to(outside)
    result=owner(deployer).run(apply=True)
    assert result['outcome']=='refused' and result['deleted']==[]
    assert (outside/'sentinel').read_bytes()==b'outside'


def test_nested_symlink_unlinks_only_its_owned_entry(layout,tmp_path):
    paths,deployer,_=layout
    outside=tmp_path/'outside';outside.mkdir();(outside/'sentinel').write_bytes(b'outside')
    (paths.releases/C/'external').symlink_to(outside,target_is_directory=True)
    result=owner(deployer).run(apply=True)
    assert result['deleted']==[C,D,E] and (outside/'sentinel').read_bytes()==b'outside'


def test_candidate_replacement_race_never_follows_the_replacement(layout,tmp_path,monkeypatch):
    paths,deployer,_=layout
    from lcstatus.retention import Retention
    outside=tmp_path/'outside';outside.mkdir();(outside/'sentinel').write_bytes(b'outside')
    original=Retention._remove_candidate
    def raced(self,fd,sha,snapshot,device):
        if sha==C:
            shutil.rmtree(paths.releases/C);(paths.releases/C).symlink_to(outside)
        return original(self,fd,sha,snapshot,device)
    monkeypatch.setattr(Retention,'_remove_candidate',raced)
    result=owner(deployer).run(apply=True)
    assert result['errors'] and result['deleted']==[]
    assert (outside/'sentinel').read_bytes()==b'outside' and (paths.releases/D).exists()


def test_different_device_is_refused_before_traversal_or_deletion(layout,monkeypatch):
    import lcstatus.retention as retention
    paths,deployer,_=layout
    (paths.releases/C/'mount').mkdir()
    original=retention.entry_stat
    def other_device(fd,name):
        value=original(fd,name)
        if name=='mount':
            fields=list(value);fields[2]+=1;return os.stat_result(fields)
        return value
    monkeypatch.setattr(retention,'entry_stat',other_device)
    result=owner(deployer).run(apply=True)
    assert result['outcome']=='refused' and result['deleted']==[]
    assert all((paths.releases/sha).exists() for sha in (A,B,C,D,E))


def test_journal_must_start_before_first_deletion(layout,monkeypatch):
    from lcstatus.retention import Journal
    paths,deployer,_=layout
    monkeypatch.setattr(Journal,'append',lambda *a,**kw: (_ for _ in ()).throw(OSError('fixture audit fault')))
    result=owner(deployer).run(apply=True)
    assert result['errors'] and result['deleted']==[]
    assert all((paths.releases/sha).exists() for sha in (C,D,E))


def test_partial_delete_failure_is_journaled_and_remaining_trees_survive(layout,monkeypatch):
    from lcstatus.retention import Retention
    paths,deployer,_=layout
    original=Retention._remove_candidate
    def faulty(self,fd,sha,snapshot,device):
        if sha==D: raise OSError('fixture removal fault')
        return original(self,fd,sha,snapshot,device)
    monkeypatch.setattr(Retention,'_remove_candidate',faulty)
    result=owner(deployer).run(apply=True)
    assert result['outcome']=='partial' and result['deleted']==[C] and result['errors']
    assert (paths.releases/D).exists() and (paths.releases/E).exists()
    rows=[json.loads(line) for line in Path(result['journal']).read_text().splitlines()]
    assert [r['revision'] for r in rows if r['event']=='deleted']==[C]
    assert any(r['event']=='error' for r in rows)


def test_cli_default_preview_lock_contention_and_missing_layout(layout,tmp_path):
    paths,deployer,_=layout
    def command(root,*args):
        return subprocess.run([sys.executable,'-m','lcstatus.retention','--data-root',str(root),*args],
            capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    blocked=command(paths.root,'--apply')
    assert blocked.returncode!=0 and all((paths.releases/s).exists() for s in (C,D,E))
    deployer.lock.close()
    preview=command(paths.root)
    assert preview.returncode==0 and json.loads(preview.stdout)['candidates']==[C,D,E]
    assert not list((paths.data/'logs').glob('retention-*'))
    missing=tmp_path/'missing-root'
    assert command(missing,'--apply').returncode!=0 and not missing.exists()


def test_successful_tick_prunes_after_terminal_record_and_pending_cleanup(world,monkeypatch):
    work,paths,deployer,command=world
    old=bootstrap(world)
    second=commit(work);assert deployer.tick(collect=True)==0
    third=commit(work)
    assert deployer.tick(collect=True)==0
    assert not (paths.releases/old).exists()
    assert paths.current.resolve().name==third and paths.previous.resolve().name==second
    assert attempts(paths)[-1].revision==third and attempts(paths)[-1].detail['outcome']=='deployed'
    assert not deployer.pending.exists()
    journals=sorted((paths.data/'logs').glob('retention-*'))
    assert journals
    assert any(old in p.read_text() and '"event": "deleted"' in p.read_text() for p in journals)


@pytest.mark.parametrize('fault', ['suite','environment','collection','render','pending'])
def test_failed_or_recovered_tick_never_prunes_even_when_old_collection_succeeds(world,monkeypatch,fault):
    from lcstatus.retention import Retention
    work,paths,deployer,command=world
    old=bootstrap(world)
    candidate=commit(work,'fail' if fault=='suite' else 'pass')
    command.env_fails=fault=='environment';command.collection_fails=fault=='collection';command.render_fails=fault=='render'
    if fault=='pending':
        deployer.pending.write_text(json.dumps(deployer.context(candidate,candidate,old)))
    monkeypatch.setattr(Retention,'run',lambda *a,**kw: pytest.fail('retention ran during a failed/recovery tick'))
    deployer.tick(collect=True)
    assert (paths.releases/old).exists()
