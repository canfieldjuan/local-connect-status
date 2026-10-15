"""Preview obsolete owned dashboard releases; deletion requires --apply.

All traversal and removal is relative to opened directories, without following links.
This is cooperative controller cleanup under its collection lock, not a security
boundary against another process with the same account deliberately moving open trees.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import uuid
from contextlib import ExitStack
from pathlib import Path

from .evidence import collection_lock
from .paths import Paths
from .sources import is_full_sha


class Refused(ValueError):
    pass


def entry_stat(fd: int, name: str):
    return os.stat(name, dir_fd=fd, follow_symlinks=False)


def identity(info) -> tuple:
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def signature(info) -> tuple:
    return identity(info) + (info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def mount_id(fd: int) -> str:
    # Linux exposes the mount identity of this already-open descriptor. Unlike
    # st_dev alone, this also detects a bind mount on the same filesystem.
    for line in Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines():
        if line.startswith('mnt_id:'):
            return line.split(':', 1)[1].strip()
    raise Refused('safe mount identity is unavailable')


def directory_flags() -> int:
    if (not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')
            or not {os.open, os.stat, os.unlink, os.rmdir, os.mkdir} <= os.supports_dir_fd
            or os.listdir not in os.supports_fd or os.stat not in os.supports_follow_symlinks):
        raise Refused('safe descriptor-relative filesystem operations are unavailable')
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def require_layout(paths: Paths) -> None:
    # Do this before the shared lock owner or Deployer can initialize a missing store.
    if not all(path.is_dir() for path in (paths.root, paths.data, paths.releases)):
        raise Refused('retention requires an existing runtime, data and releases layout')


class Journal:
    def __init__(self, data: Path):
        flags = directory_flags()
        with ExitStack() as stack:
            data_fd = os.open(data, flags)
            stack.callback(os.close, data_fd)
            try:
                os.mkdir('logs', mode=0o700, dir_fd=data_fd)
            except FileExistsError:
                pass
            logs_fd = os.open('logs', flags, dir_fd=data_fd)
            stack.callback(os.close, logs_fd)
            name = f'retention-{uuid.uuid4().hex}.jsonl'
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=logs_fd)
            self.file = os.fdopen(fd, 'w', encoding='utf-8')
            self.path = str(data / 'logs' / name)
            try:
                os.fsync(logs_fd)
            except OSError:
                self.file.close()
                raise

    def append(self, event: str, **payload) -> None:
        self.file.write(json.dumps({'event': event, **payload}, sort_keys=True) + '\n')
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self) -> None:
        self.file.close()


class Retention:
    def __init__(self, deployer, *, executing: Path | None = None):
        self.deployer = deployer
        self.paths = deployer.paths
        self.executing = executing if executing is not None else Path(__file__).resolve().parent.parent

    def _snapshot(self, parent: int, name: str, device: int) -> tuple:
        before = entry_stat(parent, name)
        if before.st_dev != device:
            raise Refused(f'different-device boundary: {name}')
        children = None
        if stat.S_ISDIR(before.st_mode):
            fd = os.open(name, directory_flags(), dir_fd=parent)
            try:
                if signature(os.fstat(fd)) != signature(before):
                    raise Refused(f'entry changed while opening: {name}')
                if mount_id(fd) != mount_id(parent):
                    raise Refused(f'nested mount boundary: {name}')
                children = {child: self._snapshot(fd, child, device) for child in sorted(os.listdir(fd))}
                if signature(os.fstat(fd)) != signature(before):
                    raise Refused(f'directory changed while planning: {name}')
            finally:
                os.close(fd)
        if signature(entry_stat(parent, name)) != signature(before):
            raise Refused(f'entry changed while planning: {name}')
        return signature(before), children

    def _receipt(self, root: int, sha: str) -> tuple | None:
        fd = os.open(sha, directory_flags(), dir_fd=root)
        try:
            try:
                info = entry_stat(fd, '.release.json')
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(info.st_mode):
                raise Refused(f'conflicting release receipt: {sha}')
            receipt_fd = os.open('.release.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(receipt_fd, 'r') as receipt:
                if signature(os.fstat(receipt.fileno())) != signature(info):
                    raise Refused(f'release receipt changed: {sha}')
                self.deployer.release(sha, receipt_fd=receipt.fileno(), receipt_only=True)
                if (signature(os.fstat(receipt.fileno())) != signature(info)
                        or signature(entry_stat(fd, '.release.json')) != signature(info)):
                    raise Refused(f'release receipt changed: {sha}')
            return signature(info)
        finally:
            os.close(fd)

    def _plan(self, root: int, result: dict) -> dict:
        if os.path.lexists(self.deployer.pending):
            raise Refused('pending deployment journal blocks retention')
        current = self.deployer.pin('current')
        if not current:
            raise Refused('retention requires a valid current pin')
        protected = {current}
        previous = self.deployer.pin('previous')
        if previous:
            protected.add(previous)
        executing = self.executing.resolve()
        releases = self.paths.releases.resolve()
        if executing.parent == releases and is_full_sha(executing.name):
            protected.add(executing.name)
        # A runtime override must never become a deletion target, even in a manually
        # assembled layout. Paths remains the sole configuration/validation owner.
        for location in (self.paths.data, self.paths.site, self.paths.cache):
            if location == releases or releases.is_relative_to(location):
                raise Refused('runtime location overlaps the releases root')
            if location.is_relative_to(releases):
                protected.add(location.relative_to(releases).parts[0])
        result['protected'] = sorted(protected)
        terminal = {record.revision for record in self.deployer.store.all()
                    if record.kind == 'deployment' and record.repo == 'local-connect-status'
                    and record.source.get('type') == 'dashboard_deploy'
                    and record.detail.get('outcome') in
                    {'deployed', 'tests-failed', 'env-failed', 'rolled-back', 'not-descendant'}}
        snapshots = {}
        device = os.fstat(root).st_dev
        for name in sorted(os.listdir(root)):
            info = entry_stat(root, name)
            if is_full_sha(name) and stat.S_ISLNK(info.st_mode):
                raise Refused(f'SHA-named symlink refuses retention: {name}')
            if not is_full_sha(name) or not stat.S_ISDIR(info.st_mode):
                result['skipped'][name] = 'not a full-SHA real release directory'
                continue
            if info.st_dev != device:
                raise Refused(f'different-device boundary: {name}')
            receipt = self._receipt(root, name)
            owned = receipt is not None or name in terminal
            if name in protected:
                continue
            if not owned:
                result['skipped'][name] = 'no controller ownership evidence'
                continue
            snapshot = self._snapshot(root, name, device)
            if receipt is not None and snapshot[1].get('.release.json', (None,))[0] != receipt:
                raise Refused(f'release receipt changed after ownership check: {name}')
            snapshots[name] = snapshot
        result['candidates'] = sorted(snapshots)
        return snapshots

    def _remove_entry(self, parent: int, name: str, snapshot: tuple, device: int) -> None:
        expected, children = snapshot
        if signature(entry_stat(parent, name)) != expected:
            raise Refused(f'entry changed before removal: {name}')
        if children is None:
            # A nested symlink is only an entry to unlink; its target is never opened.
            os.unlink(name, dir_fd=parent)
            return
        fd = os.open(name, directory_flags(), dir_fd=parent)
        try:
            if signature(os.fstat(fd)) != expected or os.fstat(fd).st_dev != device:
                raise Refused(f'directory changed before removal: {name}')
            if mount_id(fd) != mount_id(parent):
                raise Refused(f'nested mount boundary: {name}')
            if sorted(os.listdir(fd)) != sorted(children):
                raise Refused(f'directory entries changed before removal: {name}')
            for child, child_snapshot in children.items():
                self._remove_entry(fd, child, child_snapshot, device)
            # Our own unlink operations change directory times/size. Its identity must
            # still agree with both the opened directory and its parent entry.
            if identity(entry_stat(parent, name)) != expected[:3] or identity(os.fstat(fd)) != expected[:3]:
                raise Refused(f'directory replaced during removal: {name}')
            os.rmdir(name, dir_fd=parent)
        finally:
            os.close(fd)

    def _remove_candidate(self, fd: int, sha: str, snapshot: tuple, device: int) -> None:
        if self._snapshot(fd, sha, device) != snapshot:
            raise Refused(f'candidate changed after planning: {sha}')
        self._remove_entry(fd, sha, snapshot, device)

    def run(self, *, apply: bool = False) -> dict:
        result = {'outcome': 'planned', 'protected': [], 'candidates': [], 'skipped': {},
                  'deleted': [], 'errors': []}
        journal = None
        journal_attempted = False
        locked = False
        removing = None
        try:
            require_layout(self.paths)
            lock = collection_lock(self.paths.data, wait=False, inherited_fd=self.deployer.lock.fileno())
            if lock is None:
                raise Refused('collection lock is held by another collector')
            with lock, ExitStack() as stack:
                locked = True
                root_fd = os.open(self.paths.root, directory_flags())
                stack.callback(os.close, root_fd)
                fd = os.open('releases', directory_flags(), dir_fd=root_fd)
                stack.callback(os.close, fd)
                root_identity = identity(os.fstat(root_fd))
                releases_identity = identity(os.fstat(fd))
                try:
                    snapshots = self._plan(fd, result)
                except (OSError, ValueError) as exc:
                    result['outcome'] = 'refused'
                    result['errors'].append(str(exc))
                    snapshots = {}
                if apply:
                    journal_attempted = True
                    journal = Journal(self.paths.data)
                    result['journal'] = journal.path
                    journal.append('start', protected=result['protected'], candidates=result['candidates'],
                                   skipped=result['skipped'])
                    for error in result['errors']:
                        journal.append('error', reason=error)
                    for sha, snapshot in snapshots.items():
                        if (identity(os.stat(self.paths.root, follow_symlinks=False)) != root_identity
                                or identity(entry_stat(root_fd, 'releases')) != releases_identity):
                            raise Refused('owned releases root changed after planning')
                        if os.path.lexists(self.deployer.pending):
                            raise Refused('pending deployment journal blocks retention')
                        removing = sha
                        self._remove_candidate(fd, sha, snapshot, os.fstat(fd).st_dev)
                        result['deleted'].append(sha)
                        removing = None
                        journal.append('deleted', revision=sha)
                    if not result['errors']:
                        result['outcome'] = 'applied'
                    journal.append('finished', outcome=result['outcome'], deleted=result['deleted'],
                                   errors=result['errors'])
        except (OSError, ValueError) as exc:
            result['errors'].append(str(exc))
            if removing:
                result['incomplete'] = removing
            result['outcome'] = 'partial' if result['deleted'] or removing else 'refused'
            if apply and locked and (journal is not None or not journal_attempted):
                try:
                    if journal is None:
                        journal = Journal(self.paths.data)
                        result['journal'] = journal.path
                        journal.append('start', protected=result['protected'], candidates=result['candidates'],
                                       skipped=result['skipped'])
                    journal.append('error', reason=str(exc))
                    journal.append('finished', outcome=result['outcome'], deleted=result['deleted'],
                                   errors=result['errors'], incomplete=removing)
                except (OSError, ValueError) as journal_exc:
                    result['errors'].append(f'retention journal fault: {journal_exc}')
        finally:
            if journal:
                try:
                    journal.close()
                except OSError as exc:
                    result['errors'].append(f'retention journal close fault: {exc}')
                    result['outcome'] = 'partial' if result['deleted'] or removing else 'refused'
        for error in result['errors']:
            print(f'retention: {error}', file=sys.stderr)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        paths = Paths.configured(args.data_root)
        require_layout(paths)
        lock = collection_lock(paths.data, wait=False)
        if lock is None:
            raise Refused('collection lock is held by another collector')
        with lock:
            from .deploy import Deployer
            result = Retention(Deployer(paths, lock)).run(apply=args.apply)
        print(json.dumps(result, sort_keys=True))
        return 1 if result['errors'] else 0
    except (OSError, ValueError) as exc:
        print(f'retention: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
