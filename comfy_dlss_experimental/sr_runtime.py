"""Exact SR component snapshots; compiled capabilities are checked by CSR1.

The public SDK resolves the model from this directory. This file validates the
supplied DLL, not any driver/OTA model the SDK may select internally. No NR shim
is accepted as an SDK implementation and no graphics API is loaded in Python.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import uuid


def project_id(value):
    if not isinstance(value, str):
        raise ValueError("owned_sr requires compatibility.project_id as a canonical project UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("Invalid SR project UUID") from error
    if str(parsed) != value or parsed.int == 0:
        raise ValueError("SR project UUID must be canonical lowercase and nonzero")
    return value


def _hash_pe(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        if file.read(2) != b"MZ":
            raise ValueError("SR Worker and model must be Windows PE files")
        file.seek(0)
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_sr_runtime(runtime):
    if not isinstance(runtime, dict) or runtime.get("backend") != "owned_sr" or not runtime.get("ready"):
        raise ValueError("SR rendering requires a ready owned_sr Runtime Configuration")
    if runtime.get("worker_policy", "isolated") != "isolated":
        raise ValueError("SR currently uses isolated workers; disable keep_worker_alive")
    if set(runtime.get("components", {})) != {"worker", "nvngx_dlss"}:
        raise ValueError("SR requires the SDK-capable Worker and nvngx_dlss.dll, not the NR shim/model")
    project_id(runtime.get("compatibility", {}).get("project_id"))
    key = runtime.get("runtime_key")
    if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid SR runtime fingerprint")


def snapshot_sr(runtime, root):
    """Copy two verified files into a runtime-specific directory, never overwrite.

    Host execution serializes GPU work. Atomic directory publication also keeps
    parallel preflight callers from observing a partially copied runtime.
    """
    validate_sr_runtime(runtime)
    paths = {role: Path(value).expanduser().resolve(strict=True)
             for role, value in runtime["components"].items()}
    hashes = runtime.get("component_hashes", {})
    for role, path in paths.items():
        if not path.is_file() or _hash_pe(path) != hashes.get(role):
            raise ValueError(f"SR component {role} changed after Runtime Configuration; rerun configuration")
    parent = Path(root) / "runtime-snapshots"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / ("sr-" + runtime["runtime_key"])
    names = {"worker": "comfy-dlss-worker.exe", "nvngx_dlss": "nvngx_dlss.dll"}

    def verify():
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("SR snapshot must be a real directory")
        for role, name in names.items():
            path = destination / name
            if path.is_symlink() or not path.is_file() or _hash_pe(path) != hashes[role]:
                raise ValueError("SR runtime snapshot is incomplete or changed; do not overwrite it")

    if not destination.exists():
        staging = Path(tempfile.mkdtemp(prefix=".sr-", dir=parent))
        try:
            for role, name in names.items():
                shutil.copyfile(paths[role], staging / name)
                if _hash_pe(staging / name) != hashes[role]:
                    raise ValueError(f"SR component {role} changed during snapshot copy")
            try:
                os.rename(staging, destination)
            except OSError:
                # A concurrent complete snapshot may have won. Never replace
                # its contents; the following verification is authoritative.
                if not destination.exists():
                    raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    verify()
    return destination / names["worker"], destination / names["nvngx_dlss"]
