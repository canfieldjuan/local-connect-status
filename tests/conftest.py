"""Every test's default runtime locations are temporary, including subprocess HOME/XDG roots."""
import os

import pytest


@pytest.fixture(scope='session', autouse=True)
def isolated_runtime_defaults(tmp_path_factory):
    home = tmp_path_factory.mktemp('lcstatus-test-home')
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('HOME', str(home))
        patch.setenv('XDG_DATA_HOME', str(home / '.local/share'))
        patch.setenv('LCSTATUS_DATA_ROOT', str(home / 'runtime'))
        yield home
