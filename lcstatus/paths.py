"""One owner of runtime paths. Code supplies configuration, never live state."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent
ROOT_ENV = 'LCSTATUS_DATA_ROOT'


def outside_code(path: Path, code: Path = CODE_ROOT) -> Path:
    path = path.expanduser().resolve()
    if path == code.resolve() or path.is_relative_to(code.resolve()):
        raise ValueError(f'runtime path must be outside the code tree: {path}')
    # Extracted releases have no .git. Refuse their runtime writes too.
    for parent in (path, *path.parents):
        marker = parent / '.git'
        checkout = (marker.is_dir() and (marker / 'HEAD').is_file() and
                    ((marker / 'objects').is_dir() or (marker / 'commondir').is_file()))
        if marker.is_file():
            checkout = marker.read_text().startswith('gitdir: ')
        if checkout or (parent / '.release.json').exists():
            raise ValueError(f'runtime path is inside a code tree: {path}')
    return path


@dataclass(frozen=True)
class Paths:
    root: Path

    @classmethod
    def configured(cls, value: str | Path | None = None) -> 'Paths':
        root = value or os.environ.get(ROOT_ENV) or Path.home() / '.local/share/local-connect-status'
        return cls(outside_code(Path(root)))

    @property
    def data(self) -> Path:
        return outside_code(self.root / 'data')

    @property
    def cache(self) -> Path:
        return outside_code(self.root / '.cache')

    @property
    def site(self) -> Path:
        return outside_code(self.root / 'site')

    @property
    def releases(self) -> Path:
        return self.root / 'releases'

    @property
    def current(self) -> Path:
        return self.root / 'current'

    @property
    def previous(self) -> Path:
        return self.root / 'previous'
