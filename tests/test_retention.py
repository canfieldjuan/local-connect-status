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
    command.expected_current=second
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


def obsolete(paths, sha=A):
    tree=paths.releases/sha;tree.mkdir()
    (tree/'.release.json').write_text(json.dumps({'revision':sha,'tests':'pass'}))
    (tree/'duplicate-source').write_bytes(b'old archive')
    return tree


def test_absent_previous_after_bootstrap_keeps_only_current(layout):
    paths,deployer,_=layout
    paths.previous.unlink()
    result=owner(deployer).run(apply=True)
    assert result['protected']==[A] and result['deleted']==[B,C,D,E]
    assert deployer.pin('current')==A and deployer.pin('previous') is None


@pytest.mark.parametrize('receipt', ['{', '[]', '{"revision":"bad","tests":"pass"}',
                                    '{"revision":"'+C+'","tests":"fail"}'])
def test_malformed_or_conflicting_receipt_refuses_the_whole_plan(layout,receipt):
    paths,deployer,_=layout
    (paths.releases/C/'.release.json').write_text(receipt)
    result=owner(deployer).run(apply=True)
    assert result['outcome']=='refused' and result['deleted']==[] and result['errors']
    assert all((paths.releases/s).is_dir() for s in (A,B,C,D,E))


def test_files_special_entries_and_foreign_records_are_not_release_ownership(layout):
    paths,deployer,_=layout
    (paths.releases/C/'.release.json').unlink()
    for repo,source in [('other',{'type':'dashboard_deploy'}),('local-connect-status',{'type':'manual'})]:
        deployer.store.add(Record(kind='deployment',repo=repo,revision=C,verdict='fail',
                                 source=source,detail={'outcome':'tests-failed'}))
    ordinary='f'*40;special='0'*40
    (paths.releases/ordinary).write_text('operator file')
    os.mkfifo(paths.releases/special)
    result=owner(deployer).run(apply=True)
    assert result['deleted']==[D,E] and set(result['skipped'])=={C,ordinary,special}
    assert (paths.releases/ordinary).read_text()=='operator file'
    assert (paths.releases/special).lstat().st_mode


def test_safe_primitives_unavailable_refuses_without_fallback(layout,monkeypatch):
    paths,deployer,_=layout
    monkeypatch.setattr(os,'supports_fd',os.supports_fd-{os.listdir})
    result=owner(deployer).run(apply=True)
    assert result['errors'] and result['deleted']==[]
    assert all((paths.releases/s).is_dir() for s in (C,D,E))


def test_same_device_mount_boundary_refuses_before_deletion(layout,monkeypatch):
    import lcstatus.retention as retention
    paths,deployer,_=layout
    mount=paths.releases/C/'mount';mount.mkdir()
    original=retention.mount_id
    def fake_mount(fd):
        return 'fixture-bind-mount' if os.fstat(fd).st_ino==mount.stat().st_ino else original(fd)
    monkeypatch.setattr(retention,'mount_id',fake_mount)
    result=owner(deployer).run(apply=True)
    assert result['outcome']=='refused' and result['deleted']==[]
    assert all((paths.releases/s).exists() for s in (C,D,E))


@pytest.mark.parametrize('fault', ['disappeared','changed'])
def test_changed_or_disappeared_candidate_stops_remaining_cleanup(layout,monkeypatch,fault):
    from lcstatus.retention import Retention
    paths,deployer,_=layout
    original=Retention._remove_candidate
    def changed(self,fd,sha,snapshot,device):
        if sha==C:
            if fault=='disappeared': shutil.rmtree(paths.releases/C)
            else: (paths.releases/C/'program').write_text('changed bytes')
        return original(self,fd,sha,snapshot,device)
    monkeypatch.setattr(Retention,'_remove_candidate',changed)
    result=owner(deployer).run(apply=True)
    assert result['errors'] and result['deleted']==[] and (paths.releases/D).is_dir()


def test_journal_failure_after_deletion_preserves_partial_truth_and_stops(layout,monkeypatch):
    from lcstatus.retention import Journal
    paths,deployer,_=layout
    append=Journal.append
    def failed(self,event,**payload):
        if event=='deleted': raise OSError('fixture post-deletion journal fault')
        return append(self,event,**payload)
    monkeypatch.setattr(Journal,'append',failed)
    result=owner(deployer).run(apply=True)
    assert result['outcome']=='partial' and result['deleted']==[C] and result['errors']
    assert not (paths.releases/C).exists() and (paths.releases/D).is_dir()
    rows=[json.loads(line) for line in Path(result['journal']).read_text().splitlines()]
    assert rows[-1]['event']=='finished' and rows[-1]['deleted']==[C]


def test_journal_creation_failure_never_deletes(layout,monkeypatch):
    from lcstatus.retention import Journal
    paths,deployer,_=layout
    def failed(*args,**kwargs): raise OSError('fixture cannot create journal')
    monkeypatch.setattr(Journal,'__init__',failed)
    result=owner(deployer).run(apply=True)
    assert result['errors'] and result['deleted']==[]
    assert all((paths.releases/s).is_dir() for s in (C,D,E))


def test_shared_lock_explicit_inheritance_matches_existing_environment_protocol(layout,tmp_path,monkeypatch):
    paths,deployer,_=layout
    with collection_lock(paths.data,wait=False,inherited_fd=deployer.lock.fileno()) as duplicate:
        assert os.fstat(duplicate.fileno()).st_ino==os.fstat(deployer.lock.fileno()).st_ino
    assert collection_lock(paths.data,wait=False) is None
    monkeypatch.setenv('LCSTATUS_COLLECTION_LOCK_FD',str(deployer.lock.fileno()))
    with collection_lock(paths.data,wait=False) as duplicate:
        assert os.fstat(duplicate.fileno()).st_ino==os.fstat(deployer.lock.fileno()).st_ino
    foreign=tmp_path/'foreign-data';foreign.mkdir();(foreign/'.lock').touch()
    with pytest.raises(ValueError,match='different store'):
        collection_lock(foreign,wait=False,inherited_fd=deployer.lock.fileno())


def test_automatic_owner_refuses_when_its_lock_does_not_name_this_store(layout,tmp_path):
    paths,deployer,_=layout
    foreign=collection_lock(tmp_path/'foreign-data',wait=False)
    with foreign:
        deployer.lock=foreign
        result=owner(deployer).run(apply=True)
    assert result['errors'] and result['deleted']==[]
    assert all((paths.releases/s).is_dir() for s in (C,D,E))


@pytest.mark.parametrize('source_failures',[False,True])
def test_unchanged_main_ordinary_tick_prunes_and_preserves_source_failure_return(world,source_failures):
    work,paths,deployer,command=world
    current=bootstrap(world);old=obsolete(paths)
    command.source_failures=source_failures
    assert deployer.tick(collect=True)==(2 if source_failures else 0)
    assert not old.exists() and deployer.pin('current')==current
    assert len(attempts(paths))==1 and attempts(paths)[0].detail['outcome']=='deployed'


@pytest.mark.parametrize('mode',['bootstrap','heavy','rollback'])
def test_nonordinary_modes_never_prune(world,monkeypatch,mode):
    from lcstatus.retention import Retention
    work,paths,deployer,command=world
    if mode!='bootstrap':
        first=bootstrap(world)
        if mode=='rollback': commit(work);assert deployer.tick(collect=True)==0
    else:
        paths.releases.mkdir(parents=True)
    old=obsolete(paths)
    monkeypatch.setattr(Retention,'run',lambda *a,**kw: pytest.fail('cleanup in nonordinary mode'))
    if mode=='rollback': assert deployer.rollback()==0
    else: assert deployer.tick(bootstrap=mode=='bootstrap',collect=mode=='heavy',heavy_only=mode=='heavy')==0
    assert old.exists()


def test_hook_observes_completed_finalization_under_lock_and_runs_only_once(world,monkeypatch):
    from lcstatus.retention import Retention
    work,paths,deployer,command=world
    old=bootstrap(world);new=commit(work)
    calls=[];run=Retention.run
    def ready(self,**kwargs):
        assert not deployer.pending.exists()
        assert attempts(paths)[-1].revision==new and attempts(paths)[-1].detail['outcome']=='deployed'
        assert json.loads((paths.site/'status.json').read_text())['dashboard_code']['deployed']==new
        assert collection_lock(paths.data,wait=False) is None
        assert command.events[-2:]==['collection','render']
        calls.append('ready');return run(self,**kwargs)
    monkeypatch.setattr(Retention,'run',ready)
    assert deployer.tick(collect=True)==0 and calls==['ready']


@pytest.mark.parametrize('fault',['fetch','lineage','append','pending-clear'])
def test_lookup_and_incomplete_finalization_never_prune(world,monkeypatch,fault):
    from lcstatus.retention import Retention
    from lcstatus.deploy import StepFailed
    work,paths,deployer,command=world
    bootstrap(world);commit(work);old=obsolete(paths)
    monkeypatch.setattr(Retention,'run',lambda *a,**kw: pytest.fail('cleanup before finalization'))
    def failed(*a,**kw): raise OSError('fixture incomplete tick')
    if fault=='fetch': monkeypatch.setattr(deployer,'fetch',failed)
    elif fault=='lineage':
        git=deployer.git
        def missing(*args):
            if args[0]=='rev-list': raise StepFailed('fixture lineage unavailable')
            return git(*args)
        monkeypatch.setattr(deployer,'git',missing)
    elif fault=='append': monkeypatch.setattr(deployer,'append',failed)
    else:
        unlink=Path.unlink
        def pending(path,*a,**kw):
            if path==deployer.pending: raise OSError('fixture incomplete tick')
            return unlink(path,*a,**kw)
        monkeypatch.setattr(Path,'unlink',pending)
    if fault in ('append','pending-clear'):
        with pytest.raises(OSError,match='incomplete tick'): deployer.tick(collect=True)
    else: assert deployer.tick(collect=True)==0
    assert old.exists()


def test_automatic_journal_failure_keeps_truthful_deploy_without_rollback_or_retry(world,monkeypatch,capsys):
    from lcstatus.retention import Journal
    work,paths,deployer,command=world
    previous=bootstrap(world);new=commit(work);old=obsolete(paths)
    calls=[]
    def failed(*args,**kwargs):
        calls.append('start');raise OSError('fixture retention audit unavailable')
    monkeypatch.setattr(Journal,'__init__',failed)
    assert deployer.tick(collect=True)==0 and calls==['start']
    assert old.exists() and deployer.pin('current')==new and deployer.pin('previous')==previous
    assert attempts(paths)[-1].detail['outcome']=='deployed'
    row=json.loads((paths.site/'status.json').read_text())['dashboard_code']
    assert row['color']=='green' and row['deployed']==new
    assert 'retention audit unavailable' in capsys.readouterr().err


def test_early_root_refusal_gets_a_journal_where_safe(layout,tmp_path):
    paths,deployer,_=layout
    paths.releases.rename(paths.root/'held-releases')
    outside=tmp_path/'outside';outside.mkdir()
    paths.releases.symlink_to(outside)
    result=owner(deployer).run(apply=True)
    assert result['errors'] and result['deleted']==[]
    rows=[json.loads(line) for line in Path(result['journal']).read_text().splitlines()]
    assert [row['event'] for row in rows]==['start','error','finished']
    assert all((paths.root/'held-releases'/s).is_dir() for s in (A,B,C,D,E))


def test_interrupted_tree_removal_reports_the_unfinished_tree_and_stops(layout,monkeypatch):
    paths,deployer,_=layout
    unlink=os.unlink
    def failed(name,*args,**kwargs):
        if name=='program': raise OSError('fixture partial-tree deletion fault')
        return unlink(name,*args,**kwargs)
    # The capability check names real primitives, so inject only after planning.
    from lcstatus.retention import Retention
    remove=Retention._remove_candidate
    def interrupt(self,fd,sha,snapshot,device):
        with monkeypatch.context() as context:
            context.setattr(os,'unlink',failed)
            context.setattr(os,'supports_dir_fd',os.supports_dir_fd|{failed})
            return remove(self,fd,sha,snapshot,device)
    monkeypatch.setattr(Retention,'_remove_candidate',interrupt)
    result=owner(deployer).run(apply=True)
    assert result['outcome']=='partial' and result['deleted']==[] and result['incomplete']==C
    assert (paths.releases/C).is_dir() and not (paths.releases/C/'.release.json').exists()
    assert (paths.releases/D).is_dir()
    rows=[json.loads(line) for line in Path(result['journal']).read_text().splitlines()]
    assert rows[-1]['event']=='finished' and rows[-1]['incomplete']==C
