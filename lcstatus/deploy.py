"""Test exact main trees and switch kept releases under the collection lock.

Operator bootstrap: python -m lcstatus.deploy --bootstrap
User units:         python -m lcstatus.deploy --collect [--heavy-only]
Operator rollback:  python -m lcstatus.deploy --rollback
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import tempfile
from contextlib import contextmanager
import uuid
from pathlib import Path

from . import catalogue as catmod
from .evidence import (Record, Store, atomic_write, check_fingerprint, collection_lock,
                       condition_fingerprint_map)
from .paths import Paths, ROOT_ENV
from .promotion import sha256_file
from .rules import dashboard_status
from .sources import is_full_sha

REPOSITORY = 'https://github.com/canfieldjuan/local-connect-status.git'


class StepFailed(RuntimeError):
    pass


class IntegrityFailed(StepFailed):
    pass


class Deployer:
    def __init__(self, paths: Paths, lock, *, repository: str = REPOSITORY, command=None):
        self.paths, self.lock, self.repository = paths, lock, repository
        self.command = command or self.run
        self.mirror = paths.cache / 'dashboard.git'
        self.store = Store(paths.data / 'records.jsonl')
        self.pending = paths.data / 'deployment-pending.json'
        self.cat = catmod.load(Path(__file__).resolve().parent.parent / 'catalogue.json')
        self.last_summary = ''

    def environment(self, *, sandbox: Path | None = None) -> dict[str, str]:
        env = dict(os.environ)
        for name in ('PYTHONPATH', 'PYTHONHOME', 'UV_PROJECT_ENVIRONMENT', 'LCSTATUS_STAGED_DEPLOYMENT',
                     'LCSTATUS_RENDER_RECEIPT', 'LCSTATUS_COLLECTION_LOCK_FD'):
            env.pop(name, None)
        env.update({ROOT_ENV: str(self.paths.root), 'PYTHONDONTWRITEBYTECODE': '1',
                    'UV_CACHE_DIR': str(self.paths.cache / 'deploy-uv'), 'UV_PYTHON_DOWNLOADS': 'never',
                    'GIT_TERMINAL_PROMPT': '0'})
        if sandbox is not None:
            locations = {
                ROOT_ENV: sandbox / 'runtime', 'HOME': sandbox / 'home',
                'XDG_DATA_HOME': sandbox / 'home/.local/share',
                'XDG_CONFIG_HOME': sandbox / 'home/.config',
                'XDG_CACHE_HOME': sandbox / 'home/.cache',
                'XDG_STATE_HOME': sandbox / 'home/.local/state',
                'XDG_RUNTIME_DIR': sandbox / 'runtime-dir', 'TMPDIR': sandbox / 'tmp',
                'UV_CACHE_DIR': sandbox / 'uv-cache',
            }
            for name, location in locations.items():
                location.mkdir(parents=True, exist_ok=True, mode=0o700)
                env[name] = str(location)
        return env

    @contextmanager
    def candidate_environment(self, context: dict, phase: str):
        self.paths.cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"candidate-{context['attempt']}-{phase}-",
                                         dir=self.paths.cache) as directory:
            yield self.environment(sandbox=Path(directory))

    def run(self, args: list[str], *, cwd: Path, timeout: int | None = None,
            env: dict[str, str] | None = None, pass_fds: tuple[int, ...] = ()) -> subprocess.CompletedProcess:
        return subprocess.run(args, cwd=cwd, env=env or self.environment(), pass_fds=pass_fds,
                              capture_output=True, text=True, timeout=timeout)

    def git(self, *args: str) -> str:
        result = self.command(['git', '--git-dir', str(self.mirror), *args],
                              cwd=self.paths.root, timeout=120)
        if result.returncode:
            raise StepFailed((result.stderr or result.stdout).strip())
        return result.stdout.strip()

    def fetch(self) -> str:
        self.paths.cache.mkdir(parents=True, exist_ok=True)
        if not self.mirror.exists():
            self.git('init', '--bare', str(self.mirror))
            self.git('remote', 'add', 'origin', self.repository)
        # The owned mirror is never a product or developer checkout.
        if self.git('remote', 'get-url', 'origin') != self.repository:
            raise StepFailed('dashboard mirror has an unexpected remote')
        self.git('fetch', '--no-tags', 'origin', '+refs/heads/main:refs/heads/main')
        sha = self.git('rev-parse', '--verify', 'refs/heads/main')
        if not is_full_sha(sha):
            raise StepFailed('fetched main did not name an exact revision')
        return sha

    def release(self, sha: str) -> Path:
        if not is_full_sha(sha):
            raise ValueError('release requires a full lowercase SHA')
        tree = self.paths.releases / sha
        receipt = json.loads((tree / '.release.json').read_text())
        if receipt.get('revision') != sha or receipt.get('tests') != 'pass':
            raise ValueError('release has no passing exact-revision suite receipt')
        if not (tree / '.venv/bin/python').is_file():
            raise ValueError('kept release environment is missing')
        return tree

    def pin(self, name: str) -> str | None:
        link = self.paths.root / name
        if not link.is_symlink():
            if link.exists():
                raise ValueError(f'{name} must be an owned release symlink')
            return None
        target = link.resolve(strict=True)
        if target.parent != self.paths.releases.resolve():
            raise ValueError(f'{name} does not point into the owned releases directory')
        self.release(target.name)
        return target.name

    def switch(self, name: str, sha: str) -> None:
        """The sole switch owner; both current and kept previous require passing receipts."""
        self.release(sha)
        link = self.paths.root / name
        temporary = self.paths.root / f'.{name}-{uuid.uuid4().hex}'
        try:
            temporary.symlink_to(Path('releases') / sha)
            os.replace(temporary, link)
            fd = os.open(self.paths.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)

    def source(self, attempt: str | None = None, *, catalogue: dict | None = None) -> dict:
        cat = catalogue or self.cat
        check = cat['dashboard']['condition']['check']
        source = {'type': 'dashboard_deploy', 'check': check,
                  'check_fingerprint': check_fingerprint(cat['checks'][check]),
                  'condition_fingerprints': condition_fingerprint_map(cat, [cat['dashboard']['condition']['id']])}
        if attempt:
            source['attempt'] = attempt
        return source

    def observation(self, head: str | None, deployed: str | None, *, error: str | None = None,
                    phase: str | None = None) -> None:
        self.store.add(Record(kind='revision', repo='local-connect-status', revision=head or '',
                              verdict='unavailable' if error else 'pass',
                              condition_ids=[self.cat['dashboard']['condition']['id']], source=self.source(),
                              summary=(f'dashboard {deployed or "absent"} -> main {head}'
                                       + (f'; {phase}: {error}' if error else '')),
                              detail={'deployed': deployed, 'main': head, 'error': error, 'phase': phase}))

    def outcome(self, context: dict, outcome: str, reason: str, *, deployed: str | None,
                catalogue: dict | None = None) -> Record:
        cat = catalogue or self.cat
        return Record(kind='deployment', repo='local-connect-status', revision=context['candidate'],
                      verdict='pass' if outcome == 'deployed' else 'fail', platform='linux',
                      source=self.source(context['attempt'], catalogue=cat),
                      condition_ids=[cat['dashboard']['condition']['id']], summary=reason,
                      duration_s=max(0, time.monotonic() - context['started']),
                      detail={'outcome': outcome, 'deployed': deployed, 'main': context['head'],
                              'previous': context.get('previous'), 'test_summary': context.get('test_summary', ''),
                              'reason': reason, 'error': context.get('observation_error')})

    def append(self, record: Record) -> None:
        if not self.store.add(record):
            raise RuntimeError('deployment attempt was unexpectedly collapsed')

    def inventory(self, tree: Path) -> dict:
        """Exact extracted bytes/types, independent of candidate-writable Git metadata."""
        entries = {}
        def visit(directory):
            for path in sorted(directory.iterdir()):
                name = path.relative_to(tree).as_posix()
                if name in ('.git', '.venv'):
                    continue  # Only the controller-created root metadata/environment.
                mode = path.lstat().st_mode
                entry = {'mode': stat.S_IMODE(mode)}
                if stat.S_ISLNK(mode):
                    entry.update(type='link', target=os.readlink(path))
                elif stat.S_ISDIR(mode):
                    entry['type'] = 'directory'
                elif stat.S_ISREG(mode):
                    entry.update(type='file', sha256=sha256_file(path))
                else:
                    entry.update(type='special', file_type=stat.S_IFMT(mode))
                entries[name] = entry
                if entry['type'] == 'directory':
                    visit(path)
        visit(tree)
        return entries

    def verify_tree(self, tree: Path, context: dict) -> None:
        before, after = context['tree_inventory'], self.inventory(tree)
        changed = sorted(name for name in before.keys() | after.keys()
                         if before.get(name) != after.get(name))
        if changed:
            raise IntegrityFailed('exact release tree changed: ' + ', '.join(changed))

    def extract(self, sha: str) -> Path:
        tree = self.paths.releases / sha
        if tree.exists():
            # Never discard current or kept fallback while retrying an unsuccessful build.
            if sha in (self.pin('current'), self.pin('previous')):
                raise StepFailed('candidate directory is an active or kept release')
            shutil.rmtree(tree)
        self.paths.releases.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(['git', '--git-dir', str(self.mirror), 'archive', sha],
                                 capture_output=True, timeout=120)
        if archive.returncode:
            raise StepFailed(archive.stderr.decode(errors='replace'))
        try:
            tree.mkdir()
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
                tar.extractall(tree, filter='data')
            if not (tree / 'uv.lock').is_file():
                raise StepFailed('candidate has no own uv.lock')
            # The preserved repository suite scans tracked files with git ls-files. Give the
            # exact archive an independent local index/HEAD; copy objects only from our owned
            # mirror, never fetch a network or weaken that scan. Suppress user templates/hooks.
            metadata_env = self.environment()
            metadata_env.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'})
            commands = (
                ['git', 'init', '--quiet', '--template=', '--initial-branch=main', str(tree)],
                ['git', '-C', str(tree), '-c', 'core.hooksPath=/dev/null', 'fetch', '--no-tags',
                 str(self.mirror), sha],
                ['git', '-C', str(tree), '-c', 'core.hooksPath=/dev/null', 'reset', '--mixed', '--quiet', sha],
            )
            for command in commands:
                result = self.command(command, cwd=self.paths.root, timeout=120, env=metadata_env)
                if result.returncode:
                    raise StepFailed(f'local exact-revision index failed: {result.stderr or result.stdout}')
            candidate_catalogue = catmod.load(tree / 'catalogue.json')
            if 'dashboard' not in candidate_catalogue:
                raise StepFailed('candidate has no deployment condition')
            return tree
        except (OSError, ValueError, StepFailed, tarfile.TarError):
            if tree.exists():
                shutil.rmtree(tree)
            raise

    def build(self, tree: Path, context: dict) -> None:
        # No unlocked resolution and no build backend downloaded outside the locked environment.
        with self.candidate_environment(context, 'build') as env:
            result = self.command(['uv', 'sync', '--locked', '--group', 'dev', '--no-install-project',
                                   '--python', sys.executable], cwd=tree, timeout=600, env=env)
        atomic_write(self.paths.data / 'logs' / f"deploy-{context['attempt']}-env.log",
                     (result.stdout or '') + (result.stderr or ''))
        if result.returncode:
            raise StepFailed((result.stderr or result.stdout).strip() or 'candidate environment failed')

    def suite(self, tree: Path, context: dict) -> bool:
        with self.candidate_environment(context, 'suite') as env:
            result = self.command([str(tree / '.venv/bin/python'), '-m', 'pytest'],
                                  cwd=tree, timeout=1800, env=env)
        output = (result.stdout or '') + (result.stderr or '')
        atomic_write(self.paths.data / 'logs' / f"deploy-{context['attempt']}-suite.log", output)
        lines = [line.strip('= ') for line in output.splitlines() if line.strip()]
        context['test_summary'] = next((line for line in reversed(lines) if 'passed' in line or 'failed' in line),
                                       lines[-1] if lines else f'pytest exit {result.returncode}')
        return result.returncode == 0

    def collect(self, sha: str, context: dict, *, render_only: bool = False, heavy_only: bool = False,
                staged: Record | None = None) -> int:
        tree = self.release(sha)
        receipt = self.paths.cache / f"deploy-{context['attempt']}-render.json"
        receipt.unlink(missing_ok=True)
        env = self.environment()
        env['LCSTATUS_COLLECTION_LOCK_FD'] = str(self.lock.fileno())
        env['LCSTATUS_RENDER_RECEIPT'] = str(receipt)
        staged_path = None
        if staged:
            staged_path = self.paths.cache / f"deploy-{context['attempt']}-terminal.json"
            atomic_write(staged_path, staged.to_json())
            env['LCSTATUS_STAGED_DEPLOYMENT'] = str(staged_path)
        args = [str(tree / '.venv/bin/python'), '-m', 'lcstatus.collect']
        if render_only:
            args.append('--render-only')
        elif heavy_only:
            args.append('--heavy-only')
        try:
            result = self.command(args, cwd=tree, env=env, pass_fds=(self.lock.fileno(),))
            atomic_write(self.paths.data / 'logs' / f"deploy-{context['attempt']}-collection.log",
                         (result.stdout or '') + (result.stderr or ''))
            if result.returncode not in (0, 2):
                raise StepFailed(f'collection exit {result.returncode}: {result.stderr or result.stdout}')
            rendered = json.loads(receipt.read_text())
            if result.returncode == 2 and rendered.get('source_failures', 0) <= 0:
                raise StepFailed('collection exit two without existing per-source failures')
            expected = {'status.json', 'report.md', 'dashboard.html', 'index.html'}
            if set(rendered.get('files', {})) != expected or any(
                    sha256_file(self.paths.site / name) != digest for name, digest in rendered['files'].items()):
                raise StepFailed('collection did not completely render its report')
            return result.returncode
        finally:
            receipt.unlink(missing_ok=True)
            if staged_path:
                staged_path.unlink(missing_ok=True)

    def finalize(self, context: dict, outcome: str, reason: str, deployed: str | None, *, render: bool = True) -> None:
        if deployed:
            self.cat = catmod.load(self.release(deployed) / 'catalogue.json')
        record = self.outcome(context, outcome, reason, deployed=deployed)
        # The failure/rollback already happened. Persist it even if the kept renderer also fails.
        self.store = Store(self.paths.data / 'records.jsonl')
        self.append(record)
        self.pending.unlink(missing_ok=True)
        if render and deployed:
            self.collect(deployed, context, render_only=True)

    def recover(self) -> None:
        if not self.pending.exists():
            return
        context = json.loads(self.pending.read_text())
        if any(r.kind == 'deployment' and r.source.get('attempt') == context['attempt'] for r in self.store.all()):
            self.pending.unlink()
            return
        previous = context.get('previous')
        current = self.pin('current')
        if context.get('collection_ready') and current == context['candidate']:
            # Both first collection and terminal rendering completed. Finish a pending journal
            # append truthfully, whether publication was bootstrap or a normal release switch.
            self.finalize(context, 'deployed', context['test_summary'], context['candidate'])
        elif previous and current == context['candidate']:
            self.switch('current', previous)
            self.finalize(context, 'rolled-back', 'interrupted first collection; restored kept release', previous)
        elif previous:
            self.finalize(context, 'env-failed', 'interrupted before publication', current)
        else:
            self.finalize(context, 'env-failed', 'interrupted bootstrap; no initial current published', None)

    def context(self, candidate: str, head: str, previous: str | None) -> dict:
        return {'attempt': uuid.uuid4().hex, 'candidate': candidate, 'head': head, 'previous': previous,
                'started': time.monotonic(), 'test_summary': ''}

    def rollback(self) -> int:
        current, previous = self.pin('current'), self.pin('previous')
        if not current or not previous or current == previous:
            raise ValueError('rollback refuses: no distinct kept release')
        observed = dashboard_status(self.cat, self.store.all())
        context = self.context(current, observed['main'] if observed else None, previous)
        context['observation_error'] = observed['observation_error'] if observed else None
        self.switch('current', previous)
        self.finalize(context, 'rolled-back', 'operator rollback to kept release', previous)
        return 0

    def tick(self, *, bootstrap: bool = False, collect: bool = False, heavy_only: bool = False) -> int:
        self.recover()
        current = self.pin('current')
        if current is None and not bootstrap:
            raise ValueError('no deployed release; the operator must explicitly bootstrap')
        if current is not None and bootstrap:
            raise ValueError('bootstrap refuses: current already exists')
        try:
            head = self.fetch()
        except (OSError, StepFailed, subprocess.TimeoutExpired) as exc:
            context = self.context('', '', current)
            self.observation(None, current, error=str(exc), phase='fetch')
            print(f'main not observed: {exc}', file=sys.stderr)
            return self.collect(current, context, heavy_only=heavy_only) if collect and current else 1
        self.observation(head, current)
        context = self.context(head, head, current)
        rolled_back = any(r.kind == 'deployment' and r.revision == head and r.detail.get('outcome') == 'rolled-back'
                          for r in self.store.all())
        if current == head or rolled_back:
            return self.collect(current, context, heavy_only=heavy_only) if collect and current else 0
        try:
            lineage = self.git('rev-list', '--first-parent', head).splitlines()
        except (OSError, StepFailed, subprocess.TimeoutExpired) as exc:
            self.observation(None, current, error=str(exc), phase='lineage')
            print(f'main ancestry not observed: {exc}', file=sys.stderr)
            return self.collect(current, context, heavy_only=heavy_only) if collect and current else 1
        if current is not None and current not in lineage:
            self.finalize(context, 'not-descendant', 'main does not descend from current on its first-parent history',
                          current, render=False)
            return self.collect(current, context, heavy_only=heavy_only) if collect else 1
        tree = None
        try:
            tree = self.extract(head)
            context['tree_inventory'] = self.inventory(tree)
            self.build(tree, context)
            self.verify_tree(tree, context)
        except IntegrityFailed as exc:
            self.finalize(context, 'tests-failed', str(exc), current, render=False)
            return self.collect(current, context, heavy_only=heavy_only) if collect and current else 1
        except (OSError, ValueError, StepFailed, tarfile.TarError, subprocess.TimeoutExpired) as exc:
            if tree and tree.exists():
                shutil.rmtree(tree)
            self.finalize(context, 'env-failed', f'extraction/environment failed: {exc}', current, render=False)
            return self.collect(current, context, heavy_only=heavy_only) if collect and current else 1
        try:
            passed = self.suite(tree, context)
            self.verify_tree(tree, context)
        except IntegrityFailed as exc:
            passed = False
            context['test_summary'] = str(exc)
        except (OSError, subprocess.TimeoutExpired) as exc:
            passed = False
            context['test_summary'] = f'suite could not complete: {exc}'
        if not passed:
            self.finalize(context, 'tests-failed', context['test_summary'], current, render=False)
            return self.collect(current, context, heavy_only=heavy_only) if collect and current else 1
        self.cat = catmod.load(tree / 'catalogue.json')
        try:
            # Journal recovery context before any post-suite release metadata can fail.
            atomic_write(self.pending, json.dumps(context))
            atomic_write(tree / '.release.json', json.dumps({'revision': head, 'tests': 'pass',
                                                           'summary': context['test_summary']}))
        except (OSError, ValueError) as exc:
            self.finalize(context, 'env-failed', f'pre-publication metadata failed: {exc}',
                          current, render=False)
            return self.collect(current, context, heavy_only=heavy_only) if collect and current else 1
        if current:
            self.switch('previous', current)
            self.switch('current', head)
        try:
            result = self.collect(head, context, heavy_only=heavy_only)
            record = self.outcome(context, 'deployed', context['test_summary'], deployed=head)
            self.collect(head, context, render_only=True, staged=record)
        except (OSError, ValueError, StepFailed, subprocess.TimeoutExpired) as exc:
            if current:
                self.switch('current', current)
                self.finalize(context, 'rolled-back', f'first collection/render failed: {exc}', current)
            else:
                self.finalize(context, 'env-failed', f'bootstrap collection/render failed: {exc}', None)
            return 1
        # Collection/render failure handling ends here. A subsequent publication/journal I/O
        # error must leave the context for recovery, never fabricate a failed first collection.
        context['collection_ready'] = True
        atomic_write(self.pending, json.dumps(context))
        if current is None:
            self.switch('current', head)
        self.store = Store(self.paths.data / 'records.jsonl')
        self.append(record)
        self.pending.unlink()
        return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-root')
    ap.add_argument('--repository', default=REPOSITORY, help='dashboard repository; fixture tests may use a local repo')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--bootstrap', action='store_true', help='operator-only first deployment')
    mode.add_argument('--rollback', action='store_true')
    mode.add_argument('--collect', action='store_true', help='check main at the start of this collection tick')
    ap.add_argument('--heavy-only', action='store_true')
    args = ap.parse_args(argv)
    if args.heavy_only and not args.collect:
        ap.error('--heavy-only requires --collect')
    try:
        paths = Paths.configured(args.data_root)
        lock = collection_lock(paths.data, wait=args.rollback or args.bootstrap or args.heavy_only)
        if lock is None:
            print('another collection is running; not starting deployment', file=sys.stderr)
            return 3
        with lock:
            deployer = Deployer(paths, lock, repository=args.repository)
            return deployer.rollback() if args.rollback else deployer.tick(
                bootstrap=args.bootstrap, collect=args.collect, heavy_only=args.heavy_only)
    except (OSError, ValueError, StepFailed) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
