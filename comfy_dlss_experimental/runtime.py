from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from .models import ProtonInstallation


class WorkerPolicy(StrEnum):
    ISOLATED = "isolated"
    PERSISTENT = "persistent"
    AUTO = "auto"


class WorkerState(StrEnum):
    ABSENT = "absent"
    STAGING = "staging"
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    RESETTING = "resetting"
    DRAINING = "draining"
    STOPPING = "stopping"
    EXITED = "exited"
    CRASHED = "crashed"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    command: tuple[str, ...]
    environment: dict[str, str]
    working_directory: Path


def format_wine_dll_overrides(overrides: Mapping[str, str]) -> str:
    """Serialize a small, explicit Wine DLL override map.

    The runtime preset owns this mapping so different ReShade/RenoDX bundles can
    use different loader rules without mutating the ComfyUI process environment.
    """

    encoded: list[str] = []
    for name, mode in overrides.items():
        normalized_name = name.strip()
        normalized_mode = mode.replace(" ", "").lower()
        if not normalized_name or any(char in normalized_name for char in "=;,\0"):
            raise ValueError(f"Invalid Wine DLL override name: {name!r}")
        tokens = normalized_mode.split(",")
        if not tokens or any(token not in {"n", "b"} for token in tokens) or len(tokens) != len(set(tokens)):
            raise ValueError(f"Invalid Wine DLL override mode for {normalized_name}: {mode!r}")
        encoded.append(f"{normalized_name}={normalized_mode}")
    return ";".join(encoded)


def build_launch_plan(
    *,
    platform_name: str,
    sidecar: Path,
    working_directory: Path,
    proton: ProtonInstallation | None = None,
    prefix: Path | None = None,
    steam_client_path: Path | None = None,
    dll_overrides: Mapping[str, str] | None = None,
) -> LaunchPlan:
    system = platform_name.lower()
    if system == "windows":
        return LaunchPlan((str(sidecar),), {}, working_directory)
    if system != "linux":
        raise ValueError(f"Unsupported execution platform: {platform_name}")
    if proton is None or prefix is None:
        raise ValueError("Linux execution requires both a Proton installation and an isolated prefix")
    environment = {"STEAM_COMPAT_DATA_PATH": str(prefix)}
    if steam_client_path is not None:
        environment["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(steam_client_path)
    if dll_overrides:
        environment["WINEDLLOVERRIDES"] = format_wine_dll_overrides(dll_overrides)
    return LaunchPlan((str(proton.executable), "run", str(sidecar)), environment, working_directory)


@dataclass(slots=True)
class WorkerRecord:
    runtime_key: str
    state: WorkerState = WorkerState.ABSENT
    jobs_completed: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    def transition(self, expected: WorkerState, target: WorkerState) -> None:
        if self.state != expected:
            raise RuntimeError(f"Invalid worker transition {self.state} -> {target}; expected {expected}")
        self.state = target
