"""Locate our prebuilt helpers, independently of user-supplied NR runtimes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys

REPO = Path(__file__).resolve().parents[1]
TARGETS = {"linux-x86_64", "windows-x86_64"}


def target_platform():
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("预编译辅助程序目前仅支持 Linux/Windows x86_64")
    system = {"linux": "linux", "win32": "windows"}.get(sys.platform)
    if system is None:
        raise RuntimeError("预编译辅助程序目前仅支持 Linux/Windows x86_64")
    return system + "-x86_64"


def validate_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError("Invalid helper release version")
    return value


def helper_names(target):
    if target not in TARGETS:
        raise ValueError("Unsupported helper target")
    return {"relay": "dlss-native-relay.exe",
            "nvof": "dlss-nvof-helper.exe" if target == "windows-x86_64" else "dlss-nvof-helper"}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_helper(role):
    target = target_platform()
    name = helper_names(target)[role]
    directory = REPO / "sidecar" / "bin" / target
    active = directory / "active.json"
    if active.is_file():
        manifest = json.loads(active.read_text(encoding="utf-8"))
        version = validate_version(manifest.get("version"))
        if manifest.get("schema_version") != 1 or manifest.get("target") != target:
            raise ValueError("Invalid installed helper manifest")
        path = directory / version / name
        if sha256(path) != manifest.get("files", {}).get(name):
            raise ValueError("已安装辅助程序校验失败；请重新安装对应发布包。")
    else:
        # Developer builds remain usable, including existing Linux deployments.
        path = REPO / "sidecar" / "build" / name
    if not path.is_file() or (sys.platform != "win32" and not os.access(path, os.X_OK)):
        raise RuntimeError("辅助程序未安装；请使用 install.py 安装本平台预编译包，或按开发文档构建。")
    return path
