"""Manual evidence is ordered by when it was observed, including backfills."""

from __future__ import annotations

import sys

import pytest

import scripts.record_observation as observation


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
            return "2026-09-01T00:00:00+00:00"

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


@pytest.mark.parametrize("value", ["not-a-time", "2026-09-08T10:47:51"])
def test_manual_observation_time_requires_valid_timezone(value):
    with pytest.raises(ValueError):
        observation.normalize_observed_at(value)


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
