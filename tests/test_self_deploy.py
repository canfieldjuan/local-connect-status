"""Contract 11 settling evidence: fixture Git trees and temporary runtime roots only."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lcstatus import collect, render
from lcstatus.deploy import Deployer, StepFailed
from lcstatus.evidence import Record, Store, collection_lock
from lcstatus.paths import CODE_ROOT, Paths, outside_code
from lcstatus.rules import dashboard_status

ROOT = Path(__file__).resolve().parent.parent


def git(root: Path, *args: str) -> str:
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True,
                            env=dict(os.environ, GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                                     GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid'))
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def fixture_catalogue() -> dict:
    real = json.loads((ROOT / 'catalogue.json').read_text())
    return {'release': {'target': 'fixture', 'required_platforms': [],
                        'automate_scope': {'decision': 'fixture', 'note': 'fixture'}},
            'repos': {}, 'apps': {}, 'tasks': [], 'checks': {'dashboard.deploy': real['checks']['dashboard.deploy']},
            'dashboard': real['dashboard']}


class FixtureCommand:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.env_fails = False
        self.collection_fails = False
        self.render_fails = False
        self.source_failures = False
        self.expected_current = None
        self.suite_checks = []
        self.events = []

    def __call__(self, args, *, cwd, timeout=None, env=None, pass_fds=()):
        # Every subprocess, including tests and the first collection, still holds the real lock.
        probe = collection_lock(self.paths.data, wait=False)
        assert probe is None, f'lock released before {args}'
        if args[0] == 'uv':
            self.events.append('env')
            assert args[1:6] == ['sync', '--locked', '--group', 'dev', '--no-install-project']
            assert (cwd / 'uv.lock').read_text() == 'fixture-lock\n'
            if self.env_fails:
                return subprocess.CompletedProcess(args, 1, '', 'fixture environment failure')
            (cwd / '.venv/bin').mkdir(parents=True)
            (cwd / '.venv/bin/python').symlink_to(sys.executable)
            return subprocess.CompletedProcess(args, 0, '', 'fixture environment ready')
        if args[-2:] == ['-m', 'pytest']:
            self.events.append('suite')
            current = self.paths.current.resolve().name if self.paths.current.is_symlink() else None
            self.suite_checks.append(current)
            assert current == self.expected_current, 'candidate switched before its suite passed'
        if 'lcstatus.collect' in args:
            self.events.append('render' if '--render-only' in args else 'collection')
            if self.collection_fails and '--render-only' not in args and cwd.name != self.expected_current:
                return subprocess.CompletedProcess(args, 1, '', 'fixture collection crash')
            if self.render_fails and cwd.name != self.expected_current:
                return subprocess.CompletedProcess(args, 0, '', '')   # no render receipt, must refuse
        actual_env = env or dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run(args, cwd=cwd, timeout=timeout, env=actual_env, pass_fds=pass_fds,
                                capture_output=True, text=True)
        if 'lcstatus.collect' in args and self.source_failures and result.returncode == 0:
            receipt = Path(env['LCSTATUS_RENDER_RECEIPT'])
            data = json.loads(receipt.read_text()); data['source_failures'] = 1
            receipt.write_text(json.dumps(data))
            result.returncode = 2
        return result


@pytest.fixture
def world(tmp_path):
    work = tmp_path / 'fixture-repository'; work.mkdir()
    git(work, 'init', '-b', 'main')
    shutil.copytree(ROOT / 'lcstatus', work / 'lcstatus', ignore=shutil.ignore_patterns('__pycache__'))
    (work / 'catalogue.json').write_text(json.dumps(fixture_catalogue()))
    (work / 'uv.lock').write_text('fixture-lock\n')
    (work / 'pyproject.toml').write_text('[tool.pytest.ini_options]\naddopts="-q -p no:cacheprovider"\n')
    (work / 'tests').mkdir()
    (work / 'result').write_text('pass')
    (work / 'tests/test_build.py').write_text("from pathlib import Path\ndef test_exact_tree():\n assert Path('result').read_text() == 'pass'\n")
    git(work, 'add', '.'); git(work, 'commit', '-m', 'fixture seed')
    paths = Paths.configured(tmp_path / 'runtime')
    lock = collection_lock(paths.data, wait=False)
    command = FixtureCommand(paths)
    deployer = Deployer(paths, lock, repository=str(work), command=command)
    yield work, paths, deployer, command
    lock.close()


def commit(work: Path, result='pass') -> str:
    (work / 'result').write_text(result)
    (work / 'tick').write_text(str(len(git(work, 'rev-list', 'HEAD').splitlines())))
    git(work, 'add', '.');git(work, 'commit', '-m', 'fixture candidate')
    return git(work, 'rev-parse', 'HEAD')


def bootstrap(world) -> str:
    work, paths, deployer, command = world
    sha = git(work, 'rev-parse', 'HEAD')
    assert deployer.tick(bootstrap=True) == 0
    assert paths.current.resolve().name == sha
    command.expected_current = sha
    return sha


def attempts(paths: Paths) -> list[Record]:
    return [r for r in Store(paths.data / 'records.jsonl').all() if r.kind == 'deployment']


def test_passing_descendant_uses_own_tree_lock_suite_before_atomic_switch_and_keeps_previous(world):
    work, paths, deployer, command = world
    old = bootstrap(world)
    sha = commit(work)
    prefix = (paths.data / 'records.jsonl').read_bytes()
    assert deployer.tick(collect=True) == 0
    assert paths.current.is_symlink() and paths.current.resolve().name == sha
    assert paths.previous.resolve().name == old
    assert paths.previous.resolve().exists()
    assert command.suite_checks == [None, old]
    assert command.events == ['env', 'suite', 'collection', 'render'] * 2
    assert (paths.data / 'records.jsonl').read_bytes().startswith(prefix)
    assert [r.detail['outcome'] for r in attempts(paths)] == ['deployed', 'deployed']
    for r in attempts(paths):
        assert r.duration_s >= 0 and 'passed' in r.detail['test_summary']
    payload = json.loads((paths.site / 'status.json').read_text())
    assert payload['dashboard_code']['color'] == 'green'
    assert payload['dashboard_code']['deployed'] == payload['dashboard_code']['main'] == sha
    assert 'Dashboard code' in (paths.site / 'report.md').read_text()
    assert 'Dashboard code' in (paths.site / 'dashboard.html').read_text()


@pytest.mark.parametrize('failure,outcome', [('tests', 'tests-failed'), ('env', 'env-failed'), ('extract', 'env-failed')])
def test_failed_tests_environment_and_disk_full_leave_current_untouched_with_one_record(world, monkeypatch, failure, outcome):
    work, paths, deployer, command = world
    old = bootstrap(world)
    sha = commit(work, 'fail' if failure == 'tests' else 'pass')
    if failure == 'env': command.env_fails = True
    if failure == 'extract':
        monkeypatch.setattr(deployer, 'extract', lambda sha: (_ for _ in ()).throw(OSError('No space left on device')))
    assert deployer.tick(collect=True) == 0  # old collector still succeeds, failed deploy stays red
    assert paths.current.resolve().name == old
    assert not paths.previous.exists()
    records = attempts(paths)
    assert len(records) == 2 and records[-1].revision == sha
    assert records[-1].detail['outcome'] == outcome
    assert json.loads((paths.site / 'status.json').read_text())['dashboard_code']['color'] == 'red'
    if failure == 'extract': assert 'No space left' in records[-1].summary


def test_reachable_only_through_second_parent_is_not_a_first_parent_descendant(world):
    work, paths, deployer, command = world
    old = bootstrap(world)
    git(work, 'checkout', '--orphan', 'displaced')
    git(work, 'add', '.');git(work, 'commit', '-m', 'unrelated main')
    git(work, 'merge', '--allow-unrelated-histories', '-s', 'ours', old, '-m', 'secondary old history')
    git(work, 'branch', '-M', 'main')
    head = git(work, 'rev-parse', 'HEAD')
    assert git(work, 'merge-base', '--is-ancestor', old, head) == ''
    assert old not in git(work, 'rev-list', '--first-parent', head).splitlines()
    before = len(command.suite_checks)
    assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == old
    assert len(command.suite_checks) == before
    assert attempts(paths)[-1].detail['outcome'] == 'not-descendant'
    assert attempts(paths)[-1].revision == head


@pytest.mark.parametrize('mode', ['exit', 'render'])
def test_first_collection_or_render_failure_rolls_back_and_never_retries_but_later_head_can_deploy(world, mode):
    work, paths, deployer, command = world
    old = bootstrap(world)
    sha = commit(work)
    command.collection_fails = mode == 'exit'; command.render_fails = mode == 'render'
    assert deployer.tick(collect=True) == 1
    assert paths.current.resolve().name == old and paths.previous.resolve().name == old
    assert [r.detail['outcome'] for r in attempts(paths)] == ['deployed', 'rolled-back']
    assert attempts(paths)[-1].revision == sha
    assert json.loads((paths.site / 'status.json').read_text())['dashboard_code']['color'] == 'red'
    command.collection_fails = command.render_fails = False
    count = len(command.suite_checks)
    assert deployer.tick(collect=True) == 0
    assert len(command.suite_checks) == count and len(attempts(paths)) == 2
    later = commit(work)
    assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == later
    assert attempts(paths)[-1].detail['outcome'] == 'deployed'


def test_manual_rollback_restores_kept_release_and_refuses_without_one(world):
    work, paths, deployer, command = world
    old = bootstrap(world)
    before = (paths.data / 'records.jsonl').read_bytes()
    with pytest.raises(ValueError, match='no distinct kept release'):
        deployer.rollback()
    assert (paths.data / 'records.jsonl').read_bytes() == before
    new = commit(work)
    assert deployer.tick() == 0
    assert deployer.rollback() == 0
    assert paths.current.resolve().name == old
    assert attempts(paths)[-1].revision == new and attempts(paths)[-1].detail['outcome'] == 'rolled-back'
    assert len(attempts(paths)) == 3
    (paths.releases / old / '.release.json').unlink()
    with pytest.raises(FileNotFoundError): deployer.rollback()
    assert len(attempts(paths)) == 3


@pytest.mark.parametrize('mode', ['exit', 'render'])
def test_operator_only_bootstrap_failure_never_publishes_current_or_fakes_rollback(world, mode):
    work, paths, deployer, command = world
    with pytest.raises(ValueError, match='operator must explicitly bootstrap'):
        deployer.tick(collect=True)
    assert attempts(paths) == []
    command.collection_fails = mode == 'exit'; command.render_fails = mode == 'render'
    assert deployer.tick(bootstrap=True) == 1
    assert not paths.current.exists() and not paths.previous.exists()
    assert len(attempts(paths)) == 1 and attempts(paths)[0].detail['outcome'] == 'env-failed'


def test_existing_per_source_failures_do_not_rollback_a_complete_collection(world):
    work, paths, deployer, command = world
    old = bootstrap(world)
    command.source_failures = True
    new = commit(work)
    assert deployer.tick(collect=True) == 2
    assert paths.current.resolve().name == new
    assert attempts(paths)[-1].detail['outcome'] == 'deployed'


def test_interrupted_pending_switch_recovers_kept_release_once_and_does_not_retry(world):
    work, paths, deployer, command = world
    old = bootstrap(world); new = commit(work)
    assert deployer.tick() == 0
    context = deployer.context(new, new, old)
    deployer.pending.write_text(json.dumps(context))
    assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == old
    assert attempts(paths)[-1].detail['outcome'] == 'rolled-back'
    assert not deployer.pending.exists()
    count = len(attempts(paths));suite_count = len(command.suite_checks)
    assert deployer.tick(collect=True) == 0
    assert len(attempts(paths)) == count and len(command.suite_checks) == suite_count


def test_dashboard_labels_require_typed_evidence_and_last_failure_clears_only_on_later_deploy(world):
    work, paths, deployer, command = world
    old = bootstrap(world)
    cat = fixture_catalogue()
    ahead = 'b' * 40
    deployer.observation(ahead, old)
    assert dashboard_status(cat, deployer.store.all())['color'] == 'amber'
    for outcome in ('tests-failed', 'env-failed', 'rolled-back', 'not-descendant'):
        context = deployer.context(ahead, ahead, old)
        record = deployer.outcome(context, outcome, 'fixture reason', deployed=old)
        rows = deployer.store.all() + [record]
        assert dashboard_status(cat, rows)['color'] == 'red'
        assert dashboard_status(cat, rows)['reason'] == 'fixture reason'
        record.source['condition_fingerprints'] = {}
        assert dashboard_status(cat, rows)['color'] == 'amber'
    green = deployer.outcome(deployer.context(ahead, ahead, old), 'deployed', 'fixture pass', deployed=ahead)
    assert dashboard_status(cat, deployer.store.all() + [green])['color'] == 'green'


def migration_module():
    spec = importlib.util.spec_from_file_location('migrate_data', ROOT / 'scripts/migrate_data.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_migration_verifies_every_file_preserves_originals_and_collection_only_writes_external_root(tmp_path, monkeypatch):
    migration = migration_module()
    original = tmp_path / 'old-code';original.mkdir()
    git(original, 'init', '-b', 'main')
    for name in ('data', '.cache', 'site'): (original / name).mkdir()
    (original / 'data/.lock').write_text('')
    (original / 'data/records.jsonl').write_text('')
    (original / 'data/state.json').write_text(json.dumps({'heads': {}, 'runs': 3}))
    (original / '.cache/test-file').write_bytes(b'cache')
    (original / 'site/index.html').write_text('old site')
    git(original, 'add', '.');git(original, 'commit', '-m', 'old code and data')
    before = migration.inventory(original)
    paths = Paths.configured(tmp_path / 'runtime')
    manifest = migration.migrate(original, paths.root)
    assert manifest == before == migration.inventory(original) == migration.inventory(paths.root)
    assert json.loads((paths.root / 'migration-hashes.json').read_text())['verified'] is True
    cat = tmp_path / 'catalogue.json';cat.write_text(json.dumps(fixture_catalogue()))
    assert collect.main(['--catalogue', str(cat), '--data-root', str(paths.root), '--no-fetch']) == 0
    assert git(original, 'status', '--porcelain') == ''
    assert before == migration.inventory(original)
    assert json.loads((paths.data / 'state.json').read_text())['runs'] == 4
    assert (paths.site / 'status.json').exists()


def test_migration_mismatch_leaves_no_destination_and_old_layout_unchanged(tmp_path, monkeypatch):
    migration = migration_module()
    original = tmp_path / 'old';(original / 'data').mkdir(parents=True)
    (original / 'data/.lock').write_text('')
    (original / 'data/records.jsonl').write_text('immutable fixture')
    destination = tmp_path / 'runtime'
    copytree = migration.shutil.copytree
    def corrupt(source, target, **kwargs):
        result = copytree(source, target, **kwargs)
        (target / 'records.jsonl').write_text('corrupt')
        return result
    monkeypatch.setattr(migration.shutil, 'copytree', corrupt)
    with pytest.raises(ValueError, match='hash mismatch'): migration.migrate(original, destination)
    assert not destination.exists()
    assert (original / 'data/records.jsonl').read_text() == 'immutable fixture'
    assert not list(tmp_path.glob('.lcstatus-migrate-*'))


def test_path_owner_refuses_code_tree_and_symlink_back_into_code_before_any_write(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='outside the code tree'): Paths.configured(CODE_ROOT / 'data')
    outside = tmp_path / 'link';outside.symlink_to(CODE_ROOT / 'data', target_is_directory=True)
    with pytest.raises(ValueError): outside_code(outside)
    with pytest.raises(ValueError):
        collect.main(['--data', str(CODE_ROOT / 'data'), '--site', str(tmp_path / 'site'), '--render-only'])
    assert not (tmp_path / 'site').exists()
    monkeypatch.setenv('LCSTATUS_DATA_ROOT', str(tmp_path / 'configured'))
    assert Paths.configured().root == tmp_path / 'configured'
    assert Paths.configured().data == tmp_path / 'configured/data'


def test_user_unit_installer_uses_configured_root_with_spaces_and_does_not_start_before_bootstrap(tmp_path):
    home = tmp_path / 'fake home';home.mkdir()
    root = home / 'runtime data'
    bins = tmp_path / 'fake-bin';bins.mkdir()
    commands = tmp_path / 'systemctl-calls'
    (bins / 'systemctl').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FIXTURE_CALLS"\n')
    (bins / 'systemctl').chmod(0o755)
    result = subprocess.run(['bash', str(ROOT / 'systemd/install.sh'), '--no-start'],
                            env=dict(os.environ, HOME=str(home), LCSTATUS_DATA_ROOT=str(root),
                                     FIXTURE_CALLS=str(commands), PATH=str(bins)+os.pathsep+os.environ['PATH']),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--now' not in commands.read_text()
    for name in ('local-connect-status.service', 'local-connect-status-heavy.service', 'local-connect-status-web.service'):
        unit = (home / '.config/systemd/user' / name).read_text()
        assert f'WorkingDirectory="{root}/current"' in unit
        assert f'ExecStart="{root}/current/.venv/bin/python"' in unit
        assert f'Environment="LCSTATUS_DATA_ROOT={root}"' in unit
    web = (home / '.config/systemd/user/local-connect-status-web.service').read_text()
    assert f'--directory "{root}/site" --bind 127.0.0.1' in web
    assert f'ExecStartPre=/usr/bin/mkdir -p "{root}/site"' in web
    assert not (ROOT / 'vercel.json').exists()


def test_path_owner_distinguishes_incomplete_git_marker_from_a_real_code_checkout(tmp_path):
    parent = tmp_path / 'marker';parent.mkdir()
    (parent / '.git').mkdir()
    assert outside_code(parent / 'runtime') == parent / 'runtime'
    git(parent, 'init', '-b', 'main')
    with pytest.raises(ValueError, match='inside a code tree'):
        outside_code(parent / 'runtime')


def test_disk_full_inside_extraction_discards_partial_candidate_and_keeps_running_release(world, monkeypatch):
    import tarfile
    work, paths, deployer, command = world
    old = bootstrap(world); new = commit(work)
    def disk_full(tar, target, **kwargs):
        (target / 'partial').write_text('incomplete extraction')
        raise OSError('No space left on device')
    monkeypatch.setattr(tarfile.TarFile, 'extractall', disk_full)
    assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == old
    assert not (paths.releases / new).exists()
    assert attempts(paths)[-1].detail['outcome'] == 'env-failed'


def test_rollback_record_survives_failure_of_the_kept_renderer(world, monkeypatch):
    work, paths, deployer, command = world
    old = bootstrap(world); new = commit(work)
    def collection_failed(*args, **kwargs):
        raise StepFailed('fixture rendering fails in both releases')
    monkeypatch.setattr(deployer, 'collect', collection_failed)
    with pytest.raises(StepFailed, match='both releases'):
        deployer.tick(collect=True)
    assert paths.current.resolve().name == old
    assert len(attempts(paths)) == 2
    assert attempts(paths)[-1].revision == new
    assert attempts(paths)[-1].detail['outcome'] == 'rolled-back'
    assert not deployer.pending.exists()


def test_explicit_legacy_record_directory_selects_all_runtime_paths_without_live_defaults(tmp_path, monkeypatch):
    sentinel = tmp_path / 'must-not-exist'
    isolated = tmp_path / 'isolated'
    monkeypatch.setenv('LCSTATUS_DATA_ROOT', str(sentinel))
    cat = tmp_path / 'catalogue.json';cat.write_text(json.dumps(fixture_catalogue()))
    paths = Paths.configured(data=isolated / 'records', site=isolated / 'report')
    assert paths.root == isolated and paths.cache == isolated / '.cache'
    assert collect.main(['--catalogue', str(cat), '--data', str(paths.data),
                         '--site', str(paths.site), '--render-only']) == 0
    assert (isolated / '.cache/mirrors').is_dir()
    assert (isolated / 'records/.lock').is_file()
    assert (isolated / 'report/status.json').is_file()
    assert not sentinel.exists()
