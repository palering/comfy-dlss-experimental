"""Explicit stdlib-only helper installer. Never run automatically at import.

Requires an independently obtained archive SHA-256. Downloads only on explicit
--url; never installs drivers, Python packages, Proton or third-party runtimes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
import zipfile

from comfy_dlss_experimental.helper_artifacts import (
    REPO, TARGETS, helper_names, sha256, target_platform, validate_version,
)

MAX_ARCHIVE = 128 * 1024**2
MAX_EXPANDED = 256 * 1024**2


def https_url(value):
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Download URL must be HTTPS without embedded credentials")
    return value


class HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url, destination):
    with build_opener(HTTPSRedirect()).open(https_url(url), timeout=30) as response, destination.open("xb") as stream:
        size = 0
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > MAX_ARCHIVE:
                raise ValueError("Helper archive is too large")
            stream.write(block)


def install_archive(archive, expected_sha256, *, target=None, install_root=None):
    target = target or target_platform()
    if target not in TARGETS:
        raise ValueError("Unsupported target")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("Provide the 64-character SHA-256 from the trusted release")
    archive = Path(archive)
    if archive.stat().st_size > MAX_ARCHIVE or sha256(archive) != expected_sha256.lower():
        raise ValueError("Helper archive SHA-256/size verification failed")
    root = Path(install_root) if install_root is not None else REPO / "sidecar" / "bin"
    names = set(helper_names(target).values())
    allowed = names | {"manifest.json", "THIRD_PARTY_NOTICES.txt"}
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        if len(infos) != len(allowed) or {item.filename for item in infos} != allowed:
            raise ValueError("Unexpected or missing archive members; external DLLs are not helper assets")
        if sum(item.file_size for item in infos) > MAX_EXPANDED:
            raise ValueError("Expanded helper archive is too large")
        for item in infos:
            mode = stat.S_IFMT(item.external_attr >> 16)
            if item.is_dir() or mode not in (0, stat.S_IFREG) or item.flag_bits & 1:
                raise ValueError("Links, directories and encrypted members are not allowed")
        if bundle.getinfo("manifest.json").file_size > 65536:
            raise ValueError("Manifest too large")
        manifest = json.loads(bundle.read("manifest.json"))
        version = validate_version(manifest.get("version"))
        if manifest.get("schema_version") != 1 or manifest.get("target") != target:
            raise ValueError("Archive target/schema does not match installation target")
        if set(manifest.get("files", {})) != names | {"THIRD_PARTY_NOTICES.txt"}:
            raise ValueError("Incomplete helper manifest")
        destination = root / target / version
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Extract only allowlisted flat filenames into a private staging folder.
        with tempfile.TemporaryDirectory(prefix=".install-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "payload"
            staged.mkdir()
            for name in allowed:
                with bundle.open(name) as source, (staged / name).open("xb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
                if name != "manifest.json" and sha256(staged / name) != manifest["files"][name]:
                    raise ValueError(f"Helper member checksum mismatch: {name}")
                if name in names:
                    (staged / name).chmod(0o755)
            if destination.exists():
                if not all((destination / name).is_file() and sha256(destination / name) == sha256(staged / name)
                           for name in allowed):
                    raise FileExistsError("Version already exists with different files; no files were overwritten")
            else:
                staged.rename(destination)
            # Last step: activate an intact version. Old versions stay available.
            pointer = Path(temporary) / "active.json"
            pointer.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            os.replace(pointer, destination.parent / "active.json")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--archive", type=Path, help="Previously downloaded helper ZIP")
    source.add_argument("--url", help="Exact HTTPS release asset URL; never inferred from latest")
    parser.add_argument("--sha256", help="Trusted SHA256SUMS entry for the ZIP")
    args = parser.parse_args()
    if not args.archive and not args.url:
        if args.sha256:
            parser.error("--sha256 requires --archive or --url")
        print("Nodes are source-only. Native helpers require explicit installation; nothing was downloaded.")
        print("Run with --archive <helper.zip> --sha256 <trusted-hash>; see docs/distribution.en.md.")
        return  # Safe when a node manager invokes install.py without arguments.
    if not args.sha256:
        parser.error("--sha256 is required when installing a helper archive")
    # Resolve before downloading; macOS/ARM installs are not silently Linux installs.
    target = target_platform()
    if args.archive:
        result = install_archive(args.archive, args.sha256, target=target)
    else:
        with tempfile.TemporaryDirectory(prefix="comfy-dlss-download-") as temporary:
            archive = Path(temporary) / "helpers.zip"
            download(args.url, archive)
            result = install_archive(archive, args.sha256, target=target)
    print(f"Installed {target} helpers: {result}")
    print("No external runtime/driver/Proton/Python packages installed. Restart ComfyUI if it is running.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error)) from error
