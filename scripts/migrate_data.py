#!/usr/bin/env python3
"""Operator-run copy and per-file SHA-256 verification; originals stay untouched."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lcstatus.evidence import atomic_write, collection_lock
from lcstatus.paths import Paths


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for name in ('data', '.cache', 'site'):
        source = root / name
        if not source.exists():
            continue
        for p in sorted(source.rglob('*')):
            # Cache links may point at extracted owned trees. Preserve links, never follow them.
            if p.is_symlink():
                result[str(p.relative_to(root))] = 'symlink:' + hashlib.sha256(os.readlink(p).encode()).hexdigest()
            elif p.is_file():
                result[str(p.relative_to(root))] = digest(p)
    return result


def migrate(source: Path, destination: Path) -> dict[str, str]:
    source = source.expanduser().resolve()
    paths = Paths.configured(destination)
    if paths.root.exists():
        raise ValueError(f'destination already exists; no merge or overwrite: {paths.root}')
    if paths.root.is_relative_to(source) or source.is_relative_to(paths.root):
        raise ValueError('source and destination must be separate trees')
    # Originals must exist; do not create the old store or its lock.
    lock_path = source / 'data/.lock'
    if not lock_path.is_file():
        raise ValueError('source collection lock is missing; stop and identify the actual data layout')
    import fcntl
    with lock_path.open('r') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = inventory(source)
        paths.root.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.lcstatus-migrate-', dir=paths.root.parent))
        try:
            for name in ('data', '.cache', 'site'):
                if (source / name).exists():
                    shutil.copytree(source / name, staging / name, symlinks=True)
            copied = inventory(staging)
            after = inventory(source)
            if before != copied or before != after:
                raise ValueError('migration hash mismatch; no switch; originals remain in place')
            # Absolute evidence paths are immutable history. They still resolve in the originals.
            atomic_write(staging / 'migration-hashes.json', json.dumps({
                'source': str(source), 'files': before, 'verified': True}, indent=2))
            os.rename(staging, paths.root)
            return before
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--from-checkout', required=True, type=Path)
    ap.add_argument('--data-root', type=Path, default=Paths.configured().root)
    args = ap.parse_args()
    try:
        files = migrate(args.from_checkout, args.data_root)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f'Verified SHA-256 for {len(files)} files/links; migrated to {args.data_root}; originals retained')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
