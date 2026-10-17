"""Installed collector inputs must belong to its own built distribution."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv
import zipfile

import pytest

from lcstatus.verify import collector_package_fingerprint


ROOT = Path(__file__).resolve().parent.parent
UNITS = ("local-connect-status.service", "local-connect-status-heavy.service")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    work = tmp_path_factory.mktemp("wheel-build")
    source = work / "source"
    shutil.copytree(ROOT, source, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"))
    uv = shutil.which("uv")
    assert uv, "uv is required to build/install the real wheel; no silent pass or skip"
    env = dict(os.environ, HOME=str(work / "home"), UV_CACHE_DIR=str(work / "uv-cache"),
               UV_PYTHON_DOWNLOADS="never", PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    result = subprocess.run([uv, "build", "--wheel", "--python", sys.executable,
                             "--out-dir", str(work / "dist"), str(source)],
                            cwd=work, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list((work / "dist").glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0], uv, env


@pytest.fixture
def installed(wheel, tmp_path):
    artifact, uv, build_env = wheel
    environment = tmp_path / "environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / "bin/python"
    result = subprocess.run([uv, "pip", "install", "--python", str(python),
                             "--no-deps", "--no-index", str(artifact)],
                            cwd=tmp_path, env=build_env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    env = dict(build_env, LCSTATUS_DATA_ROOT=str(tmp_path / "runtime"))

    def run(code=None, *, collect_args=("--help",)):
        args = [str(python), "-I"]
        args += ["-c", code] if code is not None else ["-m", "lcstatus.collect", *collect_args]
        return subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)

    location = run("import lcstatus; print(lcstatus.__path__[0])")
    assert location.returncode == 0, location.stderr
    package = Path(location.stdout.strip())
    assert package.is_relative_to(environment)
    assert not package.is_relative_to(ROOT)
    return artifact, package, run


def test_built_installed_wheel_imports_and_starts_with_exact_unit_inputs(installed, tmp_path):
    artifact, package, run = installed
    started = run()
    assert started.returncode == 0, started.stdout + started.stderr
    fingerprint = run("from lcstatus.verify import collector_fingerprint; print(collector_fingerprint())")
    assert fingerprint.returncode == 0, fingerprint.stderr
    assert fingerprint.stdout.strip() == collector_package_fingerprint(ROOT / "lcstatus")
    with zipfile.ZipFile(artifact) as archive:
        for name in UNITS:
            expected = (ROOT / "systemd" / name).read_bytes()
            assert archive.read("lcstatus/_unit_inputs/" + name) == expected
            assert (package / "_unit_inputs" / name).read_bytes() == expected
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text(json.dumps({
        "release": {"target": "fixture", "required_platforms": [],
                    "automate_scope": {"decision": "fixture", "note": "fixture"}},
        "repos": {}, "apps": {}, "tasks": [], "checks": {},
    }))
    collected = run(collect_args=["--catalogue", str(catalogue), "--no-fetch", "--no-local"])
    assert collected.returncode == 0, collected.stdout + collected.stderr
    status = json.loads((tmp_path / "runtime/site/status.json").read_text())
    assert status["source_failures"] == [] and status["tasks"] == []


@pytest.mark.parametrize("name", UNITS)
def test_installed_wheel_missing_required_input_refuses_without_sibling_fallback(installed, name):
    _, package, run = installed
    # A neighbouring distribution's files cannot substitute for this wheel's required resource.
    sibling = package.parent / "systemd"
    sibling.mkdir()
    for unit in UNITS:
        shutil.copyfile(ROOT / "systemd" / unit, sibling / unit)
    (package / "_unit_inputs" / name).unlink()
    refused = run()
    assert refused.returncode != 0
    assert "FileNotFoundError" in refused.stderr and name in refused.stderr


@pytest.mark.parametrize("name", UNITS)
def test_installed_unit_change_changes_the_exact_distribution_fingerprint(installed, name):
    _, package, run = installed
    code = "from lcstatus.verify import collector_fingerprint; print(collector_fingerprint())"
    before = run(code)
    assert before.returncode == 0, before.stderr
    target = package / "_unit_inputs" / name
    target.write_text(target.read_text().replace("Environment=PATH=", "Environment=PATH=/fixture-changed:"))
    after = run(code)
    assert after.returncode == 0, after.stderr
    assert after.stdout.strip() != before.stdout.strip()
