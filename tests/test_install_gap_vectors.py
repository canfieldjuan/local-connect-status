"""Execute the accepted public Contract 10 vectors against the one gap owner."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from lcstatus import install_gap
from lcstatus.evidence import Record
from lcstatus.promotion import package_identity
from test_promotion import debian_package

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "docs/contracts/10-install-gap-fixtures.json").read_text())


@pytest.fixture
def vector_case(tmp_path, request):
    vector = next(c for c in VECTORS["cases"] if c["name"] == request.param)
    config = VECTORS["expansion"]
    identities = VECTORS["identities"]
    packages = {}
    for name, identity in identities.items():
        path = tmp_path / (name + ".tar")
        with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
            for member, content in identity["package_fixture"]["members_utf8"].items():
                raw = content.encode()
                entry = tarfile.TarInfo(member)
                entry.mode = 0o755; entry.mtime = 0; entry.uid = entry.gid = 0; entry.size = len(raw)
                archive.addfile(entry, io.BytesIO(raw))
        assert package_identity(path, list(identity["program_hashes"])) == (identity["package_sha256"], identity["program_hashes"])
        packages[name] = path
    predecessor = Record(kind="promotion", repo=config["repo"], revision=identities["A"]["revision"],
        verdict="pass", platform="linux", recorded_at=config["predecessor_recorded_at"],
        participants={config["repo"]: identities["A"]["revision"]}, source={"observed_by": "Fixture human"},
        detail={"members": {config["app"]: {"repo": config["repo"], "revision": identities["A"]["revision"],
                                         "program_hashes": identities["A"]["program_hashes"]}}})
    records = [predecessor] + [Record(**copy.deepcopy(VECTORS["observations"][n])) for n in vector["observation_names"]]
    events = []
    for index, name in enumerate(vector["event_names"]):
        spec = VECTORS["events"][name]
        identity = identities[spec["identity"]]
        event = {k: identity[k] for k in ("revision", "package_sha256", "program_hashes")}
        event.update(installed_at=spec["installed_at"],
                     installed_observation_record_ids=spec["installed_observation_record_ids"], receipts=[])
        contents = [("ew-package", VECTORS["package_receipts"][spec["package_receipt"]])]
        if spec.get("installed_receipt") == "installed-v1":
            contents.append(("installed-v1", {"version": 1, "app": config["app"], "repo": config["repo"],
                "platform": "linux", "installed": True,
                **{k: event[k] for k in ("revision", "installed_at", "package_sha256", "program_hashes")}}))
        if spec.get("supplemental_acceptance"):
            contents.append(("ew-acceptance", {"source": event["revision"], "verified_at": event["installed_at"],
                            "installed_bytes_match": True, "linux_installed_acceptance": "PASS"}))
        for format, receipt in contents:
            path = tmp_path / f"{index}-{format}.json";path.write_text(json.dumps(receipt))
            event["receipts"].append({"format": format, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if index == len(vector["event_names"]) - 1:
            event["package"] = str(packages[spec["identity"]])
        events.append(event)
    payload = {"version": 2, "app": config["app"], "repo": config["repo"], "platform": "linux",
               "candidate_revision": identities["D"]["revision"], "last_recorded_promotion_id": predecessor.record_id,
               "history_attested_complete": True, "unrecorded_installs": events}
    return vector, payload, predecessor, records, identities["D"]["program_hashes"], config["candidate_observed_at"]


@pytest.mark.parametrize("vector_case", [c["name"] for c in VECTORS["cases"]], indirect=True)
def test_accepted_contract_vector(vector_case, monkeypatch):
    vector, payload, predecessor, records, candidate, at = vector_case
    def verify():
        gap = install_gap.Gap.decode(payload)
        gap.verify_history(predecessor, at, records, candidate)
        return gap
    if vector["expected"] == "refuse":
        with pytest.raises(ValueError): verify()
    else:
        gap = verify()
        def no_files(*_): raise AssertionError("pure stored proof reopened a file")
        monkeypatch.setattr(install_gap, "read_regular", no_files)
        gap.validate_observations(predecessor, at, records, candidate)


@pytest.mark.parametrize("vector_case", ["truthful_observed_prefix"], indirect=True)
@pytest.mark.parametrize("ids", [None, False, 0, "", [], [False], [0], [""], ["a" * 23], ["a" * 25]])
def test_collector_reference_shape_is_closed(vector_case, ids):
    _, payload, _, _, _, _ = vector_case
    event = payload["unrecorded_installs"][0]
    event["installed_observation_record_ids"] = ids
    with pytest.raises(ValueError): install_gap.Gap.decode(payload)


@pytest.mark.parametrize("vector_case", ["truthful_observed_prefix"], indirect=True)
@pytest.mark.parametrize("source", ["match", "different", "missing", "malformed", "tar-without-source"])
def test_package_only_source_binding_uses_real_debian_parser(vector_case, tmp_path, source):
    _, payload, predecessor, records, candidate, at = vector_case
    event = payload["unrecorded_installs"][-1]
    event["receipts"] = []
    if source != "tar-without-source":
        revision = None if source == "missing" else event["revision"] if source == "match" else "f" * 40 if source == "different" else "not-a-revision"
        package = debian_package(tmp_path, {k: v.encode() for k, v in VECTORS["identities"]["C"]["package_fixture"]["members_utf8"].items()}, revision)
        event["package"] = str(package)
        event["package_sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
    def verify():install_gap.Gap.decode(payload).verify_history(predecessor, at, records, candidate)
    if source == "match": verify()
    else:
        with pytest.raises(ValueError, match="source"): verify()


@pytest.mark.parametrize("vector_case", ["truthful_observed_prefix"], indirect=True)
@pytest.mark.parametrize("field,value", [("missing", v) for v in (None, False, 0, "")] + [("errors", v) for v in (None, False, 0, "")])
def test_falsy_observation_results_cannot_substitute_for_empty_collections(vector_case, field, value):
    _, payload, predecessor, records, candidate, at = vector_case
    records[2].detail["installed"][field] = value
    with pytest.raises(ValueError):
        install_gap.Gap.decode(payload).verify_history(predecessor, at, records, candidate)


@pytest.mark.parametrize("vector_case", ["repeated_transition"], indirect=True)
def test_return_event_cannot_cite_an_earlier_unused_sample(vector_case):
    _, payload, predecessor, records, candidate, at = vector_case
    old_sample = Record(**copy.deepcopy(VECTORS["observations"]["B_same_bytes_pin_error"]))
    records.append(old_sample)
    payload["unrecorded_installs"][-1]["installed_observation_record_ids"] = [old_sample.record_id]
    with pytest.raises(ValueError, match="time binding"):
        install_gap.Gap.decode(payload).verify_history(predecessor, at, records, candidate)
