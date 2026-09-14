"""Exact CXR1 runtime snapshots, never a replacement for CNR1/CSR1 presets."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .sr_runtime import _hash_pe

SL_COMPONENTS = {
    "worker": "comfy-dlss-sl-worker.exe",
    "sl_interposer": "sl.interposer.dll",
    "sl_common": "sl.common.dll",
    "nvlowlatencyvk": "NvLowLatencyVk.dll",
    "sl_dlss": "sl.dlss.dll",
    "nvngx_dlss": "nvngx_dlss.dll",
    "sl_dlssd": "sl.dlss_d.dll",
    "nvngx_dlssd": "nvngx_dlssd.dll",
}


def project_id(value):
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or not parsed.int or str(parsed) != value:
        raise ValueError("owned_sl requires a canonical lowercase nonzero project UUID")
    return value


def validate_sl_runtime(runtime):
    if not isinstance(runtime, dict) or runtime.get("backend") != "owned_sl" or runtime.get("ready") is not True:
        raise ValueError("Reconstruction requires a ready owned_sl Runtime Configuration (CXR1)")
    if runtime.get("worker_policy", "isolated") != "isolated":
        raise ValueError("Reconstruction currently requires isolated workers; disable keep_worker_alive")
    if set(runtime.get("components", {})) != set(SL_COMPONENTS):
        raise ValueError("owned_sl requires the CXR1 Worker and complete explicit SR/RR Streamline runtime")
    project_id(runtime.get("compatibility", {}).get("project_id"))
    key = runtime.get("runtime_key")
    if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid reconstruction runtime fingerprint")


def snapshot_sl(runtime, root):
    """Verify sources and reuse a complete snapshot, never repair/overwrite one."""
    validate_sl_runtime(runtime)
    paths = {role: Path(value).expanduser().resolve(strict=True)
             for role, value in runtime["components"].items()}
    hashes = runtime.get("component_hashes", {})
    for role, path in paths.items():
        if not path.is_file() or _hash_pe(path) != hashes.get(role):
            raise ValueError(f"SL component {role} changed after Runtime Configuration; rerun configuration")
    parent = Path(root) / "runtime-snapshots"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / ("sl-" + runtime["runtime_key"])
    if not destination.exists() and not destination.is_symlink():
        staging = Path(tempfile.mkdtemp(prefix=".sl-", dir=parent))
        try:
            for role, name in SL_COMPONENTS.items():
                shutil.copyfile(paths[role], staging / name)
                if _hash_pe(staging / name) != hashes[role]:
                    raise ValueError(f"SL component {role} changed during snapshot copy")
            try:
                os.rename(staging, destination)
            except OSError:
                if not destination.exists():
                    raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("SL snapshot must be a real directory")
    for role, name in SL_COMPONENTS.items():
        path = destination / name
        if path.is_symlink() or not path.is_file() or _hash_pe(path) != hashes[role]:
            raise ValueError("SL runtime snapshot is incomplete or changed; do not overwrite it")
    return destination / SL_COMPONENTS["worker"], destination
