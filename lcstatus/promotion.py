"""Read-only promotion facts: package bytes, installed bytes, revision locks and merge history.

No product process is executed, no install tree is written, and no status label is decided here.
The recorder and collector share these adapters; rules.py owns admission and classification.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .evidence import Record, now_iso
from .sources import Failure, is_full_sha


def sha256_file(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def installed_programs(programs: list[str]) -> dict[str, Any]:
    hashes: dict[str, str] = {}
    missing: list[str] = []
    errors: dict[str, str] = {}
    for name in programs:
        try:
            hashes[name] = sha256_file(Path(name))
        except FileNotFoundError:
            missing.append(name)
        except OSError as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"
    return {"hashes": hashes, "missing": missing, "errors": errors}


def package_programs(package: Path, programs: list[str]) -> dict[str, str]:
    """Hash regular payload members in a deb or tar package, without extracting or executing.

    The catalogue names absolute installed paths; package members name the same paths relative
    to the root. Tar support also lets fixture tests exercise package matching without installs.
    Duplicate, nonregular or missing program members cannot supply an install identity.
    """
    package = Path(package)
    with package.open("rb") as stream:
        magic = stream.read(8)
    if magic == b"!<arch>\n":
        result = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(package)],
                                capture_output=True, timeout=120, check=False)
        if result.returncode:
            raise ValueError("cannot read Debian package payload")
        archive = tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:*")
    else:
        archive = tarfile.open(package, mode="r:*")
    wanted = {str(PurePosixPath(p)).lstrip("/"): p for p in programs}
    hashes: dict[str, str] = {}
    with archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("unsafe package payload path")
            name = str(path)
            if name not in wanted:
                continue
            installed_path = wanted[name]
            if installed_path in hashes or not member.isfile():
                raise ValueError("duplicate or nonregular packaged program")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("unreadable packaged program")
            with stream:
                hashes[installed_path] = hashlib.file_digest(stream, "sha256").hexdigest()
    if set(hashes) != set(programs):
        raise ValueError("package is missing a catalogue program")
    return hashes


def revision_pins(mirrors: Any, repo: str, revision: str, config: dict[str, Any]) -> dict[str, Any]:
    """Parse explicit locked identities at the matching promotion's revision, never main.

    Catalogue pin sources select locked dependency names or connect-contracts pin documents.
    No substring in code, commit title or folder can attest a pin. The returned file hashes keep
    the exact raw lock data attributable even when a lock has no cross-app dependencies.
    """
    values: dict[str, list[str]] = {}
    locks: dict[str, str] = {}
    for source in config["pin_sources"]:
        path = source["path"]
        text = mirrors.show(repo, revision, path)
        if text is None:
            raise ValueError(f"cannot read revision lock: {repo}@{revision}:{path}")
        locks[path] = hashlib.sha256(text.encode()).hexdigest()
        if source["format"] in ("uv", "cargo"):
            doc = tomllib.loads(text)
            for package in doc.get("package", []):
                name = package.get("name")
                if name not in source.get("dependencies", {}):
                    continue
                raw = package.get("source", {})
                raw = raw.get("git", "") if isinstance(raw, dict) else raw
                parsed = urlsplit(raw.removeprefix("git+"))
                pin = parsed.fragment
                if not is_full_sha(pin):
                    pin = parse_qs(parsed.query).get("rev", [""])[0]
                if not is_full_sha(pin):
                    raise ValueError(f"{path}: {name} has no exact locked revision")
                key = source["dependencies"][name]
                values.setdefault(key, []).append(pin)
        elif source["format"] == "contracts":
            doc = json.loads(text)
            if (not is_full_sha(doc.get("commit")) or not isinstance(doc.get("files"), dict)
                    or doc.get("repository") != "canfieldjuan/connect-contracts"):
                raise ValueError(f"{path}: invalid contracts pin")
            values.setdefault("connect-contracts", []).append(doc["commit"])
            for name, entry in doc["files"].items():
                digest = entry.get("sha256", "")
                if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                    raise ValueError(f"{path}: invalid schema digest")
                values.setdefault("schema:" + name, []).append(digest)
                schema_text = mirrors.show("connect-contracts", doc["commit"], entry["source_path"])
                if schema_text is None or hashlib.sha256(schema_text.encode()).hexdigest() != digest:
                    raise ValueError(f"{path}: pinned schema is missing or has a hash mismatch")
                schema = json.loads(schema_text)
                protocol = schema.get("properties", {}).get("protocol_version", {}).get("const")
                if protocol is not None:
                    if type(protocol) is not int:
                        raise ValueError(f"{path}: pinned protocol must be an integer")
                    values.setdefault("protocol", []).append(str(protocol))
        else:
            raise ValueError(f"unknown pin format: {source['format']}")
    return {"values": {key: sorted(set(pins)) for key, pins in values.items()}, "locks": locks}


def installed_observation(app: str, row: dict[str, Any], catalogue: dict[str, Any],
                          records: list[Record], mirrors: Any, github: Any,
                          heads: dict[str, str], *, platform: str = "linux") -> Record:
    from .rules import matching_promotion

    config = row["promotion"]
    observed = installed_programs(config["programs"])
    detail: dict[str, Any] = {"app": app, "installed": observed}
    match = matching_promotion(app, row, records, observed, platform)
    revision = ""
    if match is not None:
        revision = match.participants[row["repo"]]
        detail["promotion_id"] = match.record_id
        try:
            detail["pins"] = revision_pins(mirrors, row["repo"], revision, config)
        except (ValueError, OSError) as exc:
            detail["pin_error"] = str(exc)
        head = heads.get(row["repo"])
        if head is None:
            detail["history_error"] = "main head was not observed this tick"
        else:
            history = mirrors.first_parent_merges(row["repo"], revision, head)
            if isinstance(history, Failure):
                detail["history_error"] = history.why
            else:
                for merge in history:
                    pulls = github.api(f"repos/{catalogue['repos'][row['repo']]['github']}/commits/{merge['sha']}/pulls",
                                       paginate=True)
                    if isinstance(pulls, Failure):
                        detail["history_error"] = pulls.why
                        break
                    # Only the PR that actually merged as this commit supplies numbers/labels.
                    associated = [p for p in pulls if p.get("merge_commit_sha") == merge["sha"] and p.get("merged_at")]
                    merge["prs"] = [p["number"] for p in associated]
                    merge["labels"] = sorted({l["name"] for p in associated for l in p.get("labels", [])})
                detail["merges"] = history
    return Record(kind="installed_observation", repo=row["repo"], revision=revision,
                  verdict="unavailable" if observed["errors"] or detail.get("pin_error") or detail.get("history_error") else "pass",
                  platform=platform, source={"type": "installed_program_hashes", "app": app},
                  recorded_at=now_iso(), detail=detail)
