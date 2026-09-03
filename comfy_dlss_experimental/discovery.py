from __future__ import annotations

import os
import platform
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from .models import ProtonInstallation


_LIBRARY_PATH_RE = re.compile(r'^\s*"path"\s+"(?P<path>.+)"\s*$')


def _steam_bases(home: Path) -> list[Path]:
    return [
        home / ".steam/root",
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
        home / "snap/steam/common/.local/share/Steam",
    ]


def _steam_library_paths(base: Path) -> list[Path]:
    library_file = base / "steamapps/libraryfolders.vdf"
    try:
        lines = library_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    paths: list[Path] = []
    for line in lines:
        match = _LIBRARY_PATH_RE.match(line)
        if match:
            paths.append(Path(match.group("path").replace("\\\\", "\\")))
    return paths


def candidate_proton_roots(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[tuple[str, Path]]:
    """Return launcher install roots, following the layouts used by ProtonPlus."""

    home = (home or Path.home()).expanduser()
    environ = environ or os.environ
    roots: list[tuple[str, Path]] = []

    custom = environ.get("COMFY_DLSS_PROTON_PATHS", "")
    for item in custom.split(os.pathsep):
        if item.strip():
            roots.append(("custom", Path(item).expanduser()))

    steam_bases = _steam_bases(home)
    for base in steam_bases:
        roots.extend(
            [
                ("steam-custom", base / "compatibilitytools.d"),
                ("steam-managed", base / "steamapps/common"),
            ]
        )
        for library in _steam_library_paths(base):
            roots.append(("steam-library", library / "steamapps/common"))

    roots.extend(
        [
            ("lutris", home / ".local/share/lutris/runners/proton"),
            ("lutris", home / ".local/share/lutris/runners/wine"),
            ("lutris-flatpak", home / ".var/app/net.lutris.Lutris/data/lutris/runners/proton"),
            ("lutris-flatpak", home / ".var/app/net.lutris.Lutris/data/lutris/runners/wine"),
            ("heroic", home / ".config/heroic/tools/proton"),
            ("heroic-flatpak", home / ".var/app/com.heroicgameslauncher.hgl/config/heroic/tools/proton"),
        ]
    )
    return roots


def _version_for(root: Path) -> str:
    try:
        first_line = (root / "version").read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return "unknown"
    parts = first_line.strip().split(maxsplit=1)
    return parts[-1] if parts else "unknown"


def _install_candidates(root: Path) -> Sequence[Path]:
    if root.is_file() and root.name == "proton":
        return [root.parent]
    if (root / "proton").is_file():
        return [root]
    try:
        return sorted((entry for entry in root.iterdir() if entry.is_dir()), key=lambda item: item.name.casefold())
    except OSError:
        return []


def discover_proton_installations(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> list[ProtonInstallation]:
    """Find Proton installations without executing launcher or Proton code."""

    system = (platform_name or platform.system()).lower()
    if system != "linux":
        return []

    found: list[ProtonInstallation] = []
    canonical_executables: set[Path] = set()
    for source, root in candidate_proton_roots(home=home, environ=environ):
        for install_root in _install_candidates(root):
            executable = install_root / "proton"
            if not executable.is_file() or not os.access(executable, os.X_OK):
                continue
            try:
                canonical = executable.resolve()
            except OSError:
                canonical = executable.absolute()
            if canonical in canonical_executables:
                continue
            canonical_executables.add(canonical)
            found.append(
                ProtonInstallation(
                    source=source,
                    name=install_root.name,
                    version=_version_for(install_root),
                    root=install_root.resolve(),
                    executable=canonical,
                )
            )
    return found


def proton_choices() -> list[str]:
    choices = ["auto"]
    choices.extend(item.selection_id for item in discover_proton_installations())
    return choices


def custom_proton(raw_path: str) -> ProtonInstallation:
    """Accept an installed launcher or its directory, never a shell command."""
    value = raw_path.strip()
    if not value or any(c in value for c in ("\0", "\n", "\r")):
        raise ValueError("请输入 Proton 启动文件或安装目录，不要填写命令行参数。")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Proton 自定义路径必须是绝对路径（支持 ~）。")
    executable = path / "proton" if path.is_dir() else path
    executable = executable.resolve(strict=True)
    if executable.name != "proton" or not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("Proton 路径必须指向可执行的 proton 启动文件，或包含它的目录。")
    return ProtonInstallation("custom", executable.parent.name, _version_for(executable.parent),
                              executable.parent, executable)


def resolve_proton_choice(choice: str, installations: Sequence[ProtonInstallation]) -> ProtonInstallation | None:
    if choice == "auto":
        return installations[0] if installations else None
    return next((item for item in installations if item.selection_id == choice), None)
