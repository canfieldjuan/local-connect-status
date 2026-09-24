"""Manual evidence is ordered by when it was observed, including backfills."""

from __future__ import annotations

import json
import select
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.record_observation as observation


@pytest.fixture(autouse=True)
def private_data(tmp_path, monkeypatch):
    """The script takes the collection lock in its data directory; never the repository's own."""
    monkeypatch.setattr(observation, "DATA", tmp_path / "observation-data")


def test_manual_record_uses_normalized_observation_time(monkeypatch):
    catalogue = {
        "repos": {"ip": {}},
        "checks": {
            "manual.demo": {
                "runner": "manual_observation", "repo": "ip", "participants": ["ip"],
            }
        },
        "tasks": [{
            "id": "task", "conditions": [{"id": "condition", "check": "manual.demo"}],
        }],
    }
    captured = {}

    class Mirrors:
        def __init__(self, *args, **kwargs):
            pass

        def commit_time(self, repo, sha):
            return "2026-09-08T15:52:51+00:00"

    class Store:
        def __init__(self, path):
            pass

        def add(self, record):
            captured["record"] = record
            return True

    monkeypatch.setattr(observation.catmod, "load", lambda path: catalogue)
    monkeypatch.setattr(observation, "Mirrors", Mirrors)
    monkeypatch.setattr(observation, "Store", Store)
    monkeypatch.setattr(sys, "argv", [
        "record_observation.py",
        "--check", "manual.demo",
        "--participant", f"ip={'a' * 40}",
        "--verdict", "pass",
        "--platform", "linux",
        "--artifact", "artifact.txt",
        "--summary", "observed",
        "--observed-at", "2026-09-08T10:47:51-05:00",
        "--observed-by", "operator",
    ])

    assert observation.main() == 0
    record = captured["record"]
    assert record.recorded_at == "2026-09-08T15:47:51+00:00"
    assert record.source["observed_at"] == record.recorded_at
    assert set(record.source["condition_fingerprints"]) == {"condition"}


@pytest.mark.parametrize("value", ["not-a-time", "2026-09-08T10:47:51"])
def test_manual_observation_time_requires_valid_timezone(value):
    with pytest.raises(ValueError):
        observation.normalize_observed_at(value)


def test_manual_observation_time_allows_clock_skew_but_rejects_material_future():
    now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    boundary = now + observation.MAX_FUTURE_SKEW
    assert observation.normalize_observed_at(boundary.isoformat(), now=now) == boundary.isoformat()
    with pytest.raises(ValueError, match="more than 5 minutes in the future"):
        observation.normalize_observed_at((boundary + timedelta(seconds=1)).isoformat(), now=now)
    assert observation.normalize_observed_at((now - timedelta(days=30)).isoformat(), now=now)


def test_manual_observation_must_follow_every_participant_revision_with_clock_skew():
    committed = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    boundary = committed - observation.MAX_COMMIT_CLOCK_SKEW
    observation.validate_observation_after_revisions(
        boundary.isoformat(), {"ew": committed.isoformat(), "ds": boundary.isoformat()},
    )
    with pytest.raises(ValueError, match="predates the ew participant revision"):
        observation.validate_observation_after_revisions(
            (boundary - timedelta(seconds=1)).isoformat(), {"ew": committed.isoformat()},
        )
    with pytest.raises(ValueError, match="commit time must include a timezone offset"):
        observation.validate_observation_after_revisions(
            committed.isoformat(), {"ew": "2026-09-11T12:00:00"},
        )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (["ip=" + "a" * 39], "full lowercase 40-character Git SHA"),
        (["ip=" + "a" * 41], "full lowercase 40-character Git SHA"),
        (["ip=" + "g" * 40], "full lowercase 40-character Git SHA"),
        (["ip=" + "a" * 40, "ip=" + "b" * 40], "duplicate"),
        (["ip=" + "a" * 40, "<img onerror=alert(1)>=" + "b" * 40], "unexpected"),
        (["ip"], "repo=sha"),
    ],
)
def test_participant_parser_rejects_ambiguous_or_constructed_metadata(values, message):
    with pytest.raises(ValueError, match=message):
        observation.parse_participants(values, ["ip"])


def test_participant_parser_accepts_exact_catalogue_set_at_sha_boundary():
    assert observation.parse_participants(
        ["ew=" + "a" * 40, "ip=" + "b" * 40], ["ew", "ip"]
    ) == {"ew": "a" * 40, "ip": "b" * 40}


def test_manual_record_rejects_participant_revision_absent_from_owned_mirror(monkeypatch, capsys):
    catalogue = {
        "repos": {"ip": {}, "ew": {}},
        "checks": {
            "manual.demo": {
                "runner": "manual_observation", "repo": "ip", "participants": ["ip", "ew"],
            }
        },
        "tasks": [{"id": "task", "conditions": [{"id": "condition", "check": "manual.demo"}]}],
    }

    class Mirrors:
        def __init__(self, *args, **kwargs):
            pass

        def commit_time(self, repo, sha):
            return "2026-09-01T00:00:00+00:00" if repo == "ip" else None

    monkeypatch.setattr(observation.catmod, "load", lambda path: catalogue)
    monkeypatch.setattr(observation, "Mirrors", Mirrors)
    monkeypatch.setattr(sys, "argv", [
        "record_observation.py",
        "--check", "manual.demo",
        "--participant", f"ip={'a' * 40}",
        "--participant", f"ew={'b' * 40}",
        "--verdict", "pass",
        "--platform", "linux",
        "--artifact", "artifact.txt",
        "--summary", "observed",
        "--observed-at", "2026-09-08T10:47:51-05:00",
        "--observed-by", "operator",
    ])

    assert observation.main() == 2
    assert "ew revision is not present in its owned mirror" in capsys.readouterr().err


def test_manual_record_rejects_observation_before_any_participant_revision(monkeypatch, capsys):
    catalogue = {
        "repos": {"ew": {}, "ds": {}},
        "checks": {"manual.demo": {
            "runner": "manual_observation", "repo": "ew", "participants": ["ew", "ds"],
        }},
        "tasks": [{"id": "task", "conditions": [{"id": "condition", "check": "manual.demo"}]}],
    }

    class Mirrors:
        def __init__(self, *args, **kwargs):
            pass

        def commit_time(self, repo, sha):
            return "2026-09-11T12:00:00+00:00" if repo == "ew" else "2026-09-11T12:06:00+00:00"

    class Store:
        def __init__(self, path):
            raise AssertionError("invalid observation must not reach the evidence store")

    monkeypatch.setattr(observation.catmod, "load", lambda path: catalogue)
    monkeypatch.setattr(observation, "Mirrors", Mirrors)
    monkeypatch.setattr(observation, "Store", Store)
    monkeypatch.setattr(sys, "argv", [
        "record_observation.py", "--check", "manual.demo",
        "--participant", f"ew={'a' * 40}", "--participant", f"ds={'b' * 40}",
        "--verdict", "pass", "--platform", "linux", "--artifact", "artifact.txt",
        "--summary", "observed", "--observed-at", "2026-09-11T12:00:00+00:00",
        "--observed-by", "operator",
    ])

    assert observation.main() == 2
    assert "predates the ds participant revision by more than 5 minutes" in capsys.readouterr().err


# ------------------------------------------------------------------ one writer at a time (contract 08 B5)

ROOT = Path(__file__).resolve().parent.parent
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_AUTHOR_DATE": "2026-09-01T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-09-01T00:00:00+00:00",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin",
}
# The real script, pointed at a private catalogue, mirror directory and data directory.
SCRIPT = """
import sys
from pathlib import Path
import scripts.record_observation as o
o.DATA, o.CATALOGUE, o.MIRRORS = (Path(a) for a in sys.argv[1:4])
sys.argv = ["record_observation.py", *sys.argv[4:]]
sys.exit(o.main())
"""
# Another collection: holds the lock until told to go, then appends a row and exits.
HOLDER = """
import sys, time
from pathlib import Path
from lcstatus.evidence import collection_lock
data, row, go = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
held = collection_lock(data, wait=False)
print("held" if held is not None else "busy", flush=True)
while not go.exists():
    time.sleep(0.02)
with open(data / "records.jsonl", "ab") as out:
    out.write(row.read_bytes())
"""


def observation_world(tmp: Path) -> tuple[list[str], Path, Path]:
    work = tmp / "work"
    work.mkdir()
    for args in (["init", "--quiet", "-b", "main"], ["commit", "--quiet", "--allow-empty", "-m", "first"]):
        subprocess.run(["git", *args], cwd=work, env=GIT_ENV, check=True, capture_output=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, env=GIT_ENV, check=True,
                         capture_output=True, text=True).stdout.strip()
    mirrors = tmp / "mirrors"
    mirrors.mkdir()
    subprocess.run(["git", "clone", "--quiet", "--mirror", str(work), "ghost.git"], cwd=mirrors,
                   env=GIT_ENV, check=True, capture_output=True)
    catalogue = tmp / "catalogue.json"
    catalogue.write_text(json.dumps({
        "release": {"required_platforms": ["linux"], "target": "t",
                    "automate_scope": {"decision": "undecided", "note": "n", "required_for_first_release": None}},
        "repos": {"ghost": {"github": "x/ghost", "ci_workflows": []}},
        "apps": {"app": {"name": "App", "repo": "ghost"}},
        "checks": {"manual.demo": {"runner": "manual_observation", "repo": "ghost", "platform": "linux"}},
        "tasks": [{"id": "g.task", "app": "app", "layer": "standalone", "title": "T", "promise": "p",
                   "conditions": [{"id": "g.demo", "kind": "installed_demo", "check": "manual.demo"}],
                   "depends_on": [{"repo": "ghost", "paths": ["**"]}]}],
    }))
    args = ["--check", "manual.demo", "--participant", f"ghost={sha}", "--verdict", "pass",
            "--platform", "linux", "--artifact", "a.txt", "--summary", "observed",
            "--observed-at", "2026-09-02T00:00:00+00:00", "--observed-by", "operator"]
    return args, catalogue, mirrors


def read_line(stream, seconds: float = 30) -> str:
    """One line of a child's output, or a test failure after `seconds`: never a hang."""
    ready, _, _ = select.select([stream], [], [], seconds)
    assert ready, f"no output within {seconds}s"
    return stream.readline()


def finish(proc: subprocess.Popen, seconds: float = 60) -> tuple[str, str]:
    """Wait for a child with a deadline; a child that overruns it is killed, never left behind."""
    try:
        return proc.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise


def run_script(data: Path, catalogue: Path, mirrors: Path, args: list[str], **popen) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", SCRIPT, str(data), str(catalogue), str(mirrors), *args],
                            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen)


def test_observation_waits_for_the_collection_lock_and_reads_the_store_after_it(tmp_path: Path):
    args, catalogue, mirrors = observation_world(tmp_path)
    # the row this observation writes, recorded once without contention
    first = tmp_path / "first"
    out, err = finish(run_script(first, catalogue, mirrors, args))
    assert out.strip() == "stored", err
    row = first / "records.jsonl"
    assert len(row.read_text().splitlines()) == 1

    data, go = tmp_path / "data", tmp_path / "go"
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(data), str(row), str(go)],
                              cwd=ROOT, text=True, stdout=subprocess.PIPE)
    script = None
    try:
        assert read_line(holder.stdout).strip() == "held"
        script = run_script(data, catalogue, mirrors, args)
        assert "waiting for the collection lock" in read_line(script.stderr)
        assert not (data / "records.jsonl").exists()          # nothing written while it waits
        go.touch()                                             # the collection appends the same row, then ends
        out, err = finish(script)
        assert script.returncode == 0, err
        # the store was read after the lock: the row appended while it waited is the one it collapses onto
        assert out.strip() == "already recorded (identical)"
        assert (data / "records.jsonl").read_bytes() == row.read_bytes()
    finally:
        go.touch()
        children = [proc for proc in (holder, script) if proc is not None]
        for proc in children:
            if proc.poll() is None:
                proc.kill()
        for proc in children:
            proc.wait(timeout=10)


def test_observation_holds_the_lock_while_it_reads_and_writes_the_store(monkeypatch):
    catalogue = {
        "repos": {"ip": {}},
        "checks": {"manual.demo": {"runner": "manual_observation", "repo": "ip", "participants": ["ip"]}},
        "tasks": [{"id": "task", "conditions": [{"id": "condition", "check": "manual.demo"}]}],
    }
    probes: list[object] = []

    def probe() -> None:
        other = observation.collection_lock(observation.DATA, wait=False)   # a second writer
        probes.append(other)
        if other is not None:
            other.close()

    class Mirrors:
        def __init__(self, *args, **kwargs):
            probe()

        def commit_time(self, repo, sha):
            return "2026-09-08T15:52:51+00:00"

    class Store:
        def __init__(self, path):
            probe()

        def add(self, record):
            probe()
            return True

    def load(path):
        probe()
        return catalogue

    monkeypatch.setattr(observation.catmod, "load", load)
    monkeypatch.setattr(observation, "Mirrors", Mirrors)
    monkeypatch.setattr(observation, "Store", Store)
    monkeypatch.setattr(sys, "argv", [
        "record_observation.py", "--check", "manual.demo", "--participant", f"ip={'a' * 40}",
        "--verdict", "pass", "--platform", "linux", "--artifact", "artifact.txt", "--summary", "observed",
        "--observed-at", "2026-09-08T10:47:51-05:00", "--observed-by", "operator",
    ])
    assert observation.main() == 0
    assert probes == [None, None, None, None]     # catalogue, mirrors, store, append: all under the lock

