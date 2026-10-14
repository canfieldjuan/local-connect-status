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
        assert f'WorkingDirectory={root}/current' in unit
        assert f'ExecStart="{root}/current/.venv/bin/python"' in unit
        assert f'Environment="LCSTATUS_DATA_ROOT={root}"' in unit
    web = (home / '.config/systemd/user/local-connect-status-web.service').read_text()
    assert f'--directory "{root}/site" --bind 127.0.0.1' in web
    assert f'ExecStartPre=/usr/bin/mkdir -p "{root}/site"' in web
    assert not (ROOT / 'vercel.json').exists()
    analyzer = shutil.which('systemd-analyze')
    assert analyzer, 'systemd-analyze is required to verify the own user-unit syntax; no silent pass or skip'
    # A static parser manager, with every home/unit/runtime/bus lookup in the fixture. No service
    # is installed into a real user home or started. Executables exist only to satisfy parsing.
    executable = root / 'current/.venv/bin/python'
    executable.parent.mkdir(parents=True); executable.symlink_to(sys.executable)
    unit_dir = home / '.config/systemd/user'
    for target in ('default.target', 'basic.target', 'sockets.target', 'timers.target', 'shutdown.target',
                   'sysinit.target', 'network.target', 'network-online.target'):
        (unit_dir / target).write_text('[Unit]\nDescription=Fixture target\nDefaultDependencies=no\n')
    runtime = tmp_path / 'verify-runtime'; runtime.mkdir(mode=0o700)
    verify_env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                      XDG_RUNTIME_DIR=str(runtime), SYSTEMD_UNIT_PATH=str(unit_dir),
                      DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(runtime / 'system-bus'),
                      DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(runtime / 'session-bus'))
    units = [str(unit_dir / name) for name in (
        'local-connect-status.service', 'local-connect-status-heavy.service',
        'local-connect-status-web.service', 'local-connect-status.timer', 'local-connect-status-heavy.timer')]
    verified = subprocess.run([analyzer, 'verify', '--user', '--generators=no', '--man=no', *units],
                              env=verify_env, capture_output=True, text=True, timeout=30)
    assert verified.returncode == 0, verified.stderr



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


def test_fetch_failure_is_an_observation_and_unchanged_head_recovery_is_retained(world, monkeypatch):
    work, paths, deployer, command = world
    head = bootstrap(world)
    # Establish a current==main observation before the failure to exercise a return to the
    # identical earlier observation, not just the initial absent->head bootstrap observation.
    assert deployer.tick(collect=True) == 0
    fetch = deployer.fetch
    monkeypatch.setattr(deployer, 'fetch', lambda: (_ for _ in ()).throw(StepFailed('fixture fetch 502')))
    assert deployer.tick(collect=True) == 0
    code = json.loads((paths.site / 'status.json').read_text())['dashboard_code']
    assert code['color'] == 'amber' and code['label'] == 'main not observed'
    assert code['main'] is None and code['deployed'] == head and '502' in code['reason']
    assert len(attempts(paths)) == 1
    monkeypatch.setattr(deployer, 'fetch', fetch)
    assert deployer.tick(collect=True) == 0
    code = json.loads((paths.site / 'status.json').read_text())['dashboard_code']
    assert code['color'] == 'green' and code['main'] == code['deployed'] == head
    rows = [r for r in Store(paths.data / 'records.jsonl').all() if r.kind == 'revision']
    assert [r.verdict for r in rows][-3:] == ['pass', 'unavailable', 'pass']
    assert len(attempts(paths)) == 1 and len(command.suite_checks) == 1


def test_lineage_read_failure_is_an_observation_and_kept_collection_runs(world, monkeypatch):
    work, paths, deployer, command = world
    old = bootstrap(world); new = commit(work)
    original = deployer.git
    def corrupt_history(*args):
        if args[0] == 'rev-list': raise StepFailed('fixture unreadable ancestor')
        return original(*args)
    monkeypatch.setattr(deployer, 'git', corrupt_history)
    before = command.events.count('collection')
    assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == old
    assert command.events.count('collection') == before + 1
    assert len(attempts(paths)) == 1
    code = json.loads((paths.site / 'status.json').read_text())['dashboard_code']
    assert code['color'] == 'amber' and code['label'] == 'main not observed'
    assert 'unreadable ancestor' in code['reason']
    monkeypatch.setattr(deployer, 'git', original)
    assert deployer.tick(collect=True) == 0
    assert attempts(paths)[-1].revision == new and attempts(paths)[-1].detail['outcome'] == 'deployed'


def test_failed_attempt_stays_red_across_main_changes_until_later_deploy(world):
    work, paths, deployer, command = world
    old = bootstrap(world); failed = commit(work, 'fail')
    assert deployer.tick(collect=True) == 0
    assert attempts(paths)[-1].detail['outcome'] == 'tests-failed'
    assert json.loads((paths.site / 'status.json').read_text())['dashboard_code']['color'] == 'red'
    # Directly accepted Option B: only a later successful deployment clears a real failure.
    deployer.observation(failed, old)
    assert dashboard_status(fixture_catalogue(), deployer.store.all())['color'] == 'red'
    later = commit(work, 'pass')
    deployer.observation(later, old)
    status = dashboard_status(fixture_catalogue(), deployer.store.all())
    assert status['color'] == 'red' and status['label'] == 'tests-failed'
    assert status['attempt_revision'] == failed and status['outcome'] == 'tests-failed'
    deployer.observation(old, old)
    assert dashboard_status(fixture_catalogue(), deployer.store.all())['color'] == 'red'
    assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == later
    assert json.loads((paths.site / 'status.json').read_text())['dashboard_code']['color'] == 'green'


def test_candidate_build_and_suite_writes_use_private_data_home_xdg_and_cleanup(world):
    work, paths, deployer, command = world
    (work / 'tests/test_candidate_root.py').write_text(
        "import os\nfrom pathlib import Path\ndef test_candidate_write():\n"
        " Path(os.environ['LCSTATUS_DATA_ROOT']).joinpath('suite-sentinel').write_text('candidate')\n")
    git(work, 'add', '.');git(work, 'commit', '-m', 'candidate writes its configured root')
    original = deployer.command
    seen = []
    def observe_environment(args, *, cwd, **kwargs):
        env = kwargs.get('env')
        if args[0] == 'uv' or args[-2:] == ['-m', 'pytest']:
            phase = 'build' if args[0] == 'uv' else 'suite'
            seen.append((phase, dict(env)))
            if phase == 'build':
                Path(env['LCSTATUS_DATA_ROOT']).joinpath('build-sentinel').write_text('candidate build')
        return original(args, cwd=cwd, **kwargs)
    deployer.command = observe_environment
    assert deployer.tick(bootstrap=True) == 0
    assert not (paths.root / 'build-sentinel').exists()
    assert not (paths.root / 'suite-sentinel').exists()
    assert [phase for phase, _ in seen] == ['build', 'suite']
    roots = []
    for phase, env in seen:
        root = Path(env['LCSTATUS_DATA_ROOT'])
        assert root != paths.root and root.is_relative_to(paths.cache)
        sandbox = root.parent; roots.append(sandbox)
        for name in ('HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME',
                     'XDG_RUNTIME_DIR', 'TMPDIR', 'UV_CACHE_DIR'):
            assert Path(env[name]).is_relative_to(sandbox), (phase, name, env[name])
        assert 'LCSTATUS_COLLECTION_LOCK_FD' not in env
        assert not sandbox.exists(), 'candidate roots must be cleaned after the phase'
    assert roots[0] != roots[1]


@pytest.mark.parametrize('outcome', ['tests-failed', 'env-failed', 'rolled-back', 'not-descendant'])
def test_real_failure_keeps_label_and_reason_through_lookup_outage_until_later_deploy(world, outcome):
    work, paths, deployer, command = world
    old = bootstrap(world)
    failed = commit(work)
    deployer.append(deployer.outcome(deployer.context(failed, failed, old), outcome,
                                    'fixture candidate failure', deployed=old))
    for head, error in [(failed, None), ('b' * 40, None), (old, None),
                        (None, 'fixture main lookup outage')]:
        deployer.observation(head, old, error=error)
        code = dashboard_status(fixture_catalogue(), deployer.store.all())
        assert (code['color'], code['label'], code['reason']) == (
            'red', outcome, 'fixture candidate failure')
        assert code['attempt_revision'] == failed and code['observation_error'] == error
    render.render_all(paths.site, fixture_catalogue(), [], deployer.store.all(), {}, [], {}, {})
    code = json.loads((paths.site / 'status.json').read_text())['dashboard_code']
    assert code['color'] == 'red' and code['reason'] == 'fixture candidate failure'
    assert code['observation_error'] == 'fixture main lookup outage'
    report = (paths.site / 'report.md').read_text()
    assert 'fixture candidate failure' in report
    assert 'Main lookup error: fixture main lookup outage' in report
    later = commit(work)
    assert deployer.tick(collect=True) == 0
    code = json.loads((paths.site / 'status.json').read_text())['dashboard_code']
    assert code['color'] == 'green' and code['deployed'] == later
    assert code['observation_error'] is None


@pytest.mark.parametrize('malformed', [None, [], 'invalid', {}, {'id': None}, {'id': []},
                                     {'id': 42}, {'id': ''}, {'id': 'dashboard.code', 'check': []},
                                     {'id': 'dashboard.code', 'check': None},
                                     {'id': 'dashboard.code', 'check': 42}])
def test_malformed_candidate_dashboard_is_one_env_failure_and_kept_collection_continues(world, malformed):
    work, paths, deployer, command = world
    old = bootstrap(world)
    cat = fixture_catalogue()
    cat['dashboard']['condition'] = malformed
    (work / 'catalogue.json').write_text(json.dumps(cat))
    new = commit(work)
    before = command.events.count('collection')
    assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == old
    assert command.events.count('collection') == before + 1
    assert len(attempts(paths)) == 2
    assert attempts(paths)[-1].revision == new
    assert attempts(paths)[-1].detail['outcome'] == 'env-failed'
    assert 'catalogue invalid' in attempts(paths)[-1].detail['reason']
    assert not (paths.releases / new).exists()


@pytest.mark.parametrize('dashboard', [None, [], 'invalid', 42])
def test_dashboard_shape_is_rejected_by_catalogue_owner(tmp_path, dashboard):
    from lcstatus.catalogue import load
    cat = fixture_catalogue();cat['dashboard'] = dashboard
    path = tmp_path / 'catalogue.json';path.write_text(json.dumps(cat))
    with pytest.raises(ValueError, match='dashboard must be an object'):
        load(path)


def test_valid_dashboard_condition_still_participates_in_duplicate_id_validation(tmp_path):
    from lcstatus.catalogue import load
    cat = fixture_catalogue()
    cat['tasks'] = [{'id': 'fixture', 'app': 'bundle', 'layer': 'standalone',
                     'conditions': [dict(cat['dashboard']['condition'])]}]
    path = tmp_path / 'catalogue.json';path.write_text(json.dumps(cat))
    with pytest.raises(ValueError, match='duplicate condition id dashboard.code'):
        load(path)


def test_safe_archive_python_minimum_matches_locked_environment():
    import tomllib
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    lock = tomllib.loads((ROOT / 'uv.lock').read_text())
    assert project['project']['requires-python'] == lock['requires-python'] == '>=3.11.4'


@pytest.mark.parametrize('mode', ['source', 'config', 'untracked', 'deleted', 'link', 'mode', 'index'])
@pytest.mark.parametrize('phase', ['build', 'suite'])
def test_candidate_mutation_fails_exact_tree_gate_without_switch_and_keeps_collecting(world, mode, phase):
    work, paths, deployer, command = world
    old = bootstrap(world)
    (work / '.gitignore').write_text('.cache/\n.venv/\n')
    new = commit(work)
    run = deployer.command
    def mutate(args, *, cwd, **kwargs):
        result = run(args, cwd=cwd, **kwargs)
        target_phase = args[0] == 'uv' if phase == 'build' else args[-2:] == ['-m', 'pytest']
        if target_phase and result.returncode == 0:
            if mode in ('source', 'index'):
                (cwd / 'lcstatus/collect.py').write_text('raise RuntimeError("mutated candidate")\n')
                if mode == 'index': git(cwd, 'add', 'lcstatus/collect.py')
            elif mode == 'config': (cwd / 'catalogue.json').write_text('{}')
            elif mode == 'untracked':
                (cwd / '.cache').mkdir()
                (cwd / '.cache/importable.py').write_text('altered = True\n')
                assert git(cwd, 'status', '--porcelain', '--untracked-files=all') == ''
            elif mode == 'deleted': (cwd / 'result').unlink()
            elif mode == 'link':
                (cwd / 'result').unlink();(cwd / 'result').symlink_to('uv.lock')
            else: (cwd / 'result').chmod(0o700)
        return result
    deployer.command = mutate
    before = command.events.count('collection')
    assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == old and not paths.previous.exists()
    assert command.events.count('collection') == before + 1
    assert len(attempts(paths)) == 2
    assert attempts(paths)[-1].revision == new
    assert attempts(paths)[-1].detail['outcome'] == 'tests-failed'
    assert 'exact release tree changed:' in attempts(paths)[-1].detail['reason']
    assert not (paths.releases / new / '.release.json').exists()
    assert not deployer.pending.exists()


def test_passing_bootstrap_tests_that_rewrite_tracked_code_do_not_publish(world):
    work, paths, deployer, command = world
    (work / 'tests/test_mutation.py').write_text(
        "from pathlib import Path\ndef test_pass_then_write():\n"
        " assert Path('result').read_text() == 'pass'\n"
        " Path('lcstatus/collect.py').write_text('altered = True\\n')\n")
    new = commit(work)
    assert deployer.tick(bootstrap=True) == 1
    assert not paths.current.exists() and not paths.previous.exists()
    assert len(attempts(paths)) == 1
    assert attempts(paths)[0].revision == new
    assert attempts(paths)[0].detail['outcome'] == 'tests-failed'
    assert 'lcstatus/collect.py' in attempts(paths)[0].detail['reason']


@pytest.mark.parametrize('observation', ['known', 'unavailable', 'absent', 'forged'])
def test_manual_rollback_preserves_authenticated_main_or_unavailable_state_without_fetch(world, monkeypatch, observation):
    work, paths, deployer, command = world
    previous = bootstrap(world)
    current = commit(work);assert deployer.tick(collect=True) == 0
    command.expected_current = current
    failed = commit(work, 'fail');assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == current
    if observation == 'unavailable':
        deployer.observation(None, current, error='fixture fetch unavailable')
    elif observation == 'absent':
        # A new fixture store represents a release with no authenticated observation history.
        deployer.store = Store(paths.data / 'empty-fixture-records.jsonl')
    elif observation == 'forged':
        record = deployer.outcome(deployer.context('c' * 40, 'c' * 40, previous),
                                  'tests-failed', 'untrusted fixture', deployed=current)
        record.source['condition_fingerprints'] = {}
        deployer.append(record)
    monkeypatch.setattr(deployer, 'fetch', lambda: pytest.fail('rollback must not fetch'))
    assert deployer.rollback() == 0
    assert paths.current.resolve().name == previous
    record = attempts(paths)[-1]
    assert record.revision == current and record.detail['deployed'] == previous
    assert record.detail['main'] == (failed if observation in ('known', 'forged') else None)
    code = json.loads((paths.site / 'status.json').read_text())['dashboard_code']
    assert code['main'] == record.detail['main'] and code['label'] == 'rolled-back'
    assert code['observation_error'] == ('fixture fetch unavailable' if observation == 'unavailable' else None)


def test_interrupted_bootstrap_after_publication_finishes_one_deployed_record_without_rollback(world, monkeypatch):
    work, paths, deployer, command = world
    head = git(work, 'rev-parse', 'HEAD')
    append = deployer.append
    def interrupted(record):
        raise KeyboardInterrupt('fixture interruption after publication')
    monkeypatch.setattr(deployer, 'append', interrupted)
    with pytest.raises(KeyboardInterrupt, match='after publication'):
        deployer.tick(bootstrap=True)
    assert paths.current.resolve().name == head
    assert not paths.previous.exists() and attempts(paths) == []
    context = json.loads(deployer.pending.read_text())
    assert context['collection_ready'] is True
    monkeypatch.setattr(deployer, 'append', append)
    command.expected_current = head
    assert deployer.tick(collect=True) == 0
    assert not deployer.pending.exists() and not paths.previous.exists()
    assert len(attempts(paths)) == 1 and attempts(paths)[0].detail['outcome'] == 'deployed'
    assert attempts(paths)[0].source['attempt'] == context['attempt']
    assert len(command.suite_checks) == 1


@pytest.mark.parametrize('mode', ['bootstrap', 'normal'])
@pytest.mark.parametrize('fault', ['append', 'unlink'])
def test_published_release_persistence_error_recovers_one_truthful_deployed_record(world, monkeypatch, mode, fault):
    work, paths, deployer, command = world
    prior_count = 0
    if mode == 'normal':
        bootstrap(world); commit(work); prior_count = 1
    head = git(work, 'rev-parse', 'HEAD')
    if fault == 'append':
        append = deployer.append
        count = 0
        def transient_append(record):
            nonlocal count
            count += 1
            if count == 1: raise OSError('fixture transient journal I/O')
            return append(record)
        monkeypatch.setattr(deployer, 'append', transient_append)
    else:
        unlink = Path.unlink
        def pending_unlink(path, *args, **kwargs):
            if path == deployer.pending: raise OSError('fixture transient pending unlink')
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'unlink', pending_unlink)
    with pytest.raises(OSError, match='fixture transient'):
        deployer.tick(bootstrap=mode == 'bootstrap', collect=mode == 'normal')
    assert paths.current.resolve().name == head
    context = json.loads(deployer.pending.read_text())
    assert context['collection_ready'] is True
    assert all(r.detail['outcome'] == 'deployed' for r in attempts(paths))
    if fault == 'unlink': monkeypatch.setattr(Path, 'unlink', unlink)
    command.expected_current = head
    suites = len(command.suite_checks)
    assert deployer.tick(collect=True) == 0
    assert paths.current.resolve().name == head and not deployer.pending.exists()
    assert len(attempts(paths)) == prior_count + 1
    assert attempts(paths)[-1].source['attempt'] == context['attempt']
    assert attempts(paths)[-1].detail['outcome'] == 'deployed'
    assert len(command.suite_checks) == suites


@pytest.mark.parametrize('suffix', [' ', '\t', '\n', '\\invalid', '"invalid'])
def test_installer_refuses_unrepresentable_root_without_writing_or_starting_units(tmp_path, suffix):
    home = tmp_path / 'fixture-home'; home.mkdir()
    bins = tmp_path / 'fake-bin'; bins.mkdir()
    calls = tmp_path / 'calls'
    (bins / 'systemctl').write_text('#!/bin/sh\nprintf "%s\n" "$*" >> "$FIXTURE_CALLS"\n')
    (bins / 'systemctl').chmod(0o755)
    result = subprocess.run(['bash', str(ROOT / 'systemd/install.sh'), '--no-start'],
                            env=dict(os.environ, HOME=str(home), LCSTATUS_DATA_ROOT=str(tmp_path / 'runtime') + suffix,
                                     FIXTURE_CALLS=str(calls), PATH=str(bins)+os.pathsep+os.environ['PATH']),
                            capture_output=True, text=True)
    assert result.returncode != 0 and 'whitespace and control' in result.stderr
    assert not list((home / '.config/systemd/user').glob('*.service'))
    assert not calls.exists()


def test_repository_release_contract_runs_unchanged_inside_an_exact_git_archive(tmp_path):
    paths = Paths.configured(tmp_path / 'runtime')
    lock = collection_lock(paths.data, wait=False)
    with lock:
        paths.cache.mkdir(parents=True)
        git(tmp_path, 'clone', '--mirror', str(ROOT), str(paths.cache / 'dashboard.git'))
        head = git(ROOT, 'rev-parse', 'HEAD')
        deployer = Deployer(paths, lock, repository=str(ROOT))
        tree = deployer.extract(head)
        assert git(tree, 'rev-parse', 'HEAD') == head
        assert git(tree, 'ls-files', 'tests/test_release_contract.py', 'catalogue.json').splitlines() == [
            'catalogue.json', 'tests/test_release_contract.py']
        assert (tree / 'tests/test_release_contract.py').read_bytes() == (ROOT / 'tests/test_release_contract.py').read_bytes()
        assert git(tree, 'status', '--porcelain') == ''
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        env.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_release_contract.py'],
                                cwd=tree, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'passed' in result.stdout
        assert git(tree, 'status', '--porcelain') == ''


@pytest.mark.parametrize('metadata', ['pending', 'receipt'])
def test_prepublication_metadata_fault_is_one_env_failure_without_switch_and_kept_collection_continues(world, monkeypatch, metadata):
    import lcstatus.deploy as module
    work, paths, deployer, command = world
    old = bootstrap(world); new = commit(work)
    write = module.atomic_write
    injected = False
    def metadata_fault(path, text):
        nonlocal injected
        target = path == deployer.pending if metadata == 'pending' else path == paths.releases / new / '.release.json'
        if target and not injected:
            injected = True
            raise OSError('fixture transient metadata write')
        return write(path, text)
    monkeypatch.setattr(module, 'atomic_write', metadata_fault)
    collections = command.events.count('collection')
    assert deployer.tick(collect=True) == 0
    assert injected and paths.current.resolve().name == old
    assert command.events.count('collection') == collections + 1
    assert len(attempts(paths)) == 2 and attempts(paths)[-1].revision == new
    assert attempts(paths)[-1].detail['outcome'] == 'env-failed'
    assert 'pre-publication metadata' in attempts(paths)[-1].summary
    assert not deployer.pending.exists()
    assert not paths.previous.exists()


def test_prepublication_journal_is_retained_if_terminal_persistence_also_fails(world, monkeypatch):
    import lcstatus.deploy as module
    work, paths, deployer, command = world
    old = bootstrap(world); new = commit(work)
    write = module.atomic_write
    def receipt_fault(path, text):
        if path == paths.releases / new / '.release.json':
            raise OSError('fixture receipt unavailable')
        return write(path, text)
    def terminal_fault(record):
        raise OSError('fixture terminal persistence unavailable')
    monkeypatch.setattr(module, 'atomic_write', receipt_fault)
    monkeypatch.setattr(deployer, 'append', terminal_fault)
    with pytest.raises(OSError, match='terminal persistence'):
        deployer.tick(collect=True)
    assert paths.current.resolve().name == old
    assert deployer.pending.exists()
    assert json.loads(deployer.pending.read_text())['candidate'] == new
    assert len(attempts(paths)) == 1
