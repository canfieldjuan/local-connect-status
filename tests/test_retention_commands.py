"""Execute the documented operator commands against a fake deployed controller."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize('start', ['home', 'checkout'])
def test_documented_commands_import_the_deployed_release_from_any_working_directory(tmp_path, start):
    home = tmp_path / 'home'; home.mkdir()
    runtime = home / '.local/share/local-connect-status'
    tree = runtime / 'releases' / ('a' * 40)
    (tree / '.venv/bin').mkdir(parents=True)
    (tree / '.venv/bin/python').symlink_to(sys.executable)
    (runtime / 'current').symlink_to(Path('releases') / tree.name)
    checkout = home / 'old-checkout'; checkout.mkdir()
    # Neither fake module performs retention or any real deployment action.
    proof = ("import json,sys\nfrom pathlib import Path\n"
             "print(json.dumps({'module':str(Path(__file__).resolve()),"
             "'cwd':str(Path.cwd()),'args':sys.argv[1:]}))\n")
    for source in (tree, checkout):
        (source / 'lcstatus').mkdir()
        (source / 'lcstatus/__init__.py').write_text('')
        (source / 'lcstatus/retention.py').write_text(proof)
    readme = (ROOT / 'README.md').read_text()
    script = readme.split('Operator commands,', 1)[1].split('```sh\n', 1)[1].split('```', 1)[0]
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                            cwd=home if start == 'home' else checkout, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(rows) == 2
    assert all(Path(row['module']).is_relative_to(tree) for row in rows)
    assert all(Path(row['cwd']).resolve() == tree.resolve() for row in rows)
    assert [row['args'] for row in rows] == [
        ['--data-root', str(runtime)], ['--data-root', str(runtime), '--apply']]
