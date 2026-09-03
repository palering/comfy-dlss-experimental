from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import ProtonInstallation
from .presets import RuntimePreset, resolve_component_paths
from .runtime import build_launch_plan


ROLE_TARGETS = {
    "sidecar": "dlss-carrier.exe",
    "reshade_proxy": "dxgi.dll",
    "renodx_addon": "renodx-dlss5.addon64",
    "nvngx_dlss": "nvngx_dlss.dll",
    "nvngx_dlssnr": "nvngx_dlssnr.dll",
    "d3dcompiler_47": "d3dcompiler_47.dll",
}


@dataclass(frozen=True, slots=True)
class StagedJob:
    job_id: str
    directory: Path
    files: dict[str, Path]
    staging_modes: dict[str, str]


def _link_or_copy(source: Path, target: Path) -> str:
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        shutil.copy2(source, target)
        return "copy"


def stage_runtime_job(
    preset: RuntimePreset,
    *,
    preset_directory: Path,
    jobs_root: Path,
    runtime_key: str,
    job_id: str | None = None,
) -> StagedJob:
    """Create one immutable-by-convention runtime snapshot for a sidecar process."""

    unsupported = sorted(set(preset.components).difference(ROLE_TARGETS))
    if unsupported:
        raise ValueError(f"Unsupported runtime component roles: {', '.join(unsupported)}")
    resolved = resolve_component_paths(preset, base_dir=preset_directory)
    missing = [name for name, path in resolved.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Runtime component files are missing: {', '.join(sorted(missing))}")

    selected_job_id = job_id or f"bootstrap-{runtime_key[:12]}-{uuid.uuid4().hex[:8]}"
    if not selected_job_id or selected_job_id in {".", ".."} or any(
        character in selected_job_id for character in "/\\\0"
    ):
        raise ValueError("Invalid job identifier")
    jobs_root.mkdir(parents=True, exist_ok=True)
    job_directory = jobs_root / selected_job_id
    job_directory.mkdir(exist_ok=False)

    staged: dict[str, Path] = {}
    modes: dict[str, str] = {}
    for role, source in sorted(resolved.items()):
        target = job_directory / ROLE_TARGETS[role]
        if role == "sidecar":
            shutil.copy2(source.resolve(strict=True), target)
            modes[role] = "copy"
        else:
            modes[role] = _link_or_copy(source.resolve(strict=True), target)
        staged[role] = target

    metadata = {
        "schema_version": 1,
        "job_id": selected_job_id,
        "runtime_key": runtime_key,
        "preset_id": preset.preset_id,
        "backend": preset.backend,
        "components": {
            role: {
                "source": str(resolved[role]),
                "staged": str(staged[role]),
                "mode": modes[role],
            }
            for role in sorted(staged)
        },
    }
    (job_directory / "job.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return StagedJob(selected_job_id, job_directory, staged, modes)


def _proton_from_mapping(value: Mapping[str, Any]) -> ProtonInstallation:
    return ProtonInstallation(
        source=str(value["source"]),
        name=str(value["name"]),
        version=str(value["version"]),
        root=Path(str(value["root"])),
        executable=Path(str(value["executable"])),
    )


def _run_bounded(
    command: tuple[str, ...],
    *,
    environment: Mapping[str, str],
    working_directory: Path,
    timeout_seconds: float,
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        command,
        cwd=working_directory,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate()
        raise TimeoutError(f"Carrier exceeded its {timeout_seconds:g}-second timeout")
    return process.returncode, stdout[-64_000:], stderr[-64_000:]


def _tail_text_file(path: Path, *, limit: int = 32_000) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def augment_graphical_session(environment: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Fill unambiguous local display sockets without guessing among sessions."""

    augmented = dict(environment)
    detected: dict[str, str] = {}
    import platform
    if platform.system().lower() != "linux":
        return augmented, detected
    if not augmented.get("DISPLAY"):
        override = augmented.get("COMFY_DLSS_DISPLAY")
        if override:
            augmented["DISPLAY"] = override
            detected["DISPLAY"] = "COMFY_DLSS_DISPLAY"
        else:
            candidates: list[Path] = []
            x11_root = Path("/tmp/.X11-unix")
            try:
                for candidate in x11_root.glob("X*"):
                    suffix = candidate.name[1:]
                    info = candidate.stat()
                    if suffix.isdigit() and info.st_uid == os.getuid() and stat.S_ISSOCK(info.st_mode):
                        candidates.append(candidate)
            except OSError:
                candidates = []
            if len(candidates) == 1:
                augmented["DISPLAY"] = f":{candidates[0].name[1:]}"
                detected["DISPLAY"] = "single-user-x11-socket"

    if not augmented.get("WAYLAND_DISPLAY") and augmented.get("XDG_RUNTIME_DIR"):
        runtime_directory = Path(augmented["XDG_RUNTIME_DIR"])
        candidates = []
        try:
            for candidate in runtime_directory.glob("wayland-*"):
                info = candidate.stat()
                if info.st_uid == os.getuid() and stat.S_ISSOCK(info.st_mode):
                    candidates.append(candidate)
        except OSError:
            candidates = []
        if len(candidates) == 1:
            augmented["WAYLAND_DISPLAY"] = candidates[0].name
            detected["WAYLAND_DISPLAY"] = "single-user-wayland-socket"
    return augmented, detected


def run_carrier_bootstrap(
    *,
    runtime: Mapping[str, Any],
    data_root: Path,
    presents: int = 180,
    interval_ms: int = 8,
    ngx_smoke: bool = False,
    ngx_evaluations: int = 30,
    timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    if not 1 <= presents <= 10_000:
        raise ValueError("presents must be between 1 and 10000")
    if not 0 <= interval_ms <= 1_000:
        raise ValueError("interval_ms must be between 0 and 1000")
    if not 1 <= ngx_evaluations <= 1_000:
        raise ValueError("ngx_evaluations must be between 1 and 1000")
    if runtime.get("backend") != "renodx_reshade":
        raise ValueError("Carrier bootstrap currently supports only the renodx_reshade backend")

    preset_path = Path(str(runtime["preset_path"])).resolve(strict=True)
    preset = RuntimePreset.load(preset_path)
    runtime_key = str(runtime["runtime_key"])
    staged = stage_runtime_job(
        preset,
        preset_directory=preset_path.parent,
        jobs_root=data_root / "jobs",
        runtime_key=runtime_key,
    )
    result_path = staged.directory / "carrier-result.json"
    platform_name = str(runtime["platform"])
    proton_value = runtime.get("proton")
    proton = _proton_from_mapping(proton_value) if isinstance(proton_value, Mapping) else None
    requested_display_backend = str(runtime.get("linux_display_backend", "auto"))
    prefix = (
        data_root / "prefixes" / f"bootstrap-{runtime_key[:16]}-{requested_display_backend}"
        if platform_name == "linux"
        else None
    )
    if prefix is not None:
        prefix.mkdir(parents=True, exist_ok=True)
    steam_root = Path.home() / ".local" / "share" / "Steam"
    plan = build_launch_plan(
        platform_name=platform_name,
        sidecar=staged.files["sidecar"],
        working_directory=staged.directory,
        proton=proton,
        prefix=prefix,
        steam_client_path=steam_root if steam_root.is_dir() else None,
        dll_overrides=preset.wine_dll_overrides,
    )
    command = plan.command + (
        "--result",
        str(result_path),
        "--presents",
        str(presents),
        "--interval-ms",
        str(interval_ms),
    )
    if ngx_smoke:
        command += ("--ngx-smoke", "--ngx-evaluations", str(ngx_evaluations))
    environment = os.environ.copy()
    environment.update(plan.environment)
    detected_session: dict[str, str] = {}
    effective_display_backend: str | None = None
    if platform_name == "linux":
        environment, detected_session = augment_graphical_session(environment)
        if requested_display_backend in {"auto", "xwayland"}:
            effective_display_backend = "xwayland"
            if not environment.get("DISPLAY"):
                return {
                    "schema_version": 1,
                    "ok": False,
                    "stage": "graphical-session",
                    "error": "Xwayland was selected, but no DISPLAY is available to the ComfyUI process.",
                    "job_id": staged.job_id,
                    "job_directory": str(staged.directory),
                }
            environment["PROTON_ENABLE_WAYLAND"] = "0"
            environment["PROTON_USE_WAYLAND"] = "0"
            environment["WINE_GRAPHICS_DRIVER"] = "x11"
        elif requested_display_backend == "wayland_native_experimental":
            effective_display_backend = "wayland_native"
            if not environment.get("WAYLAND_DISPLAY"):
                return {
                    "schema_version": 1,
                    "ok": False,
                    "stage": "graphical-session",
                    "error": "Native Wayland was selected, but no WAYLAND_DISPLAY is available to the ComfyUI process.",
                    "job_id": staged.job_id,
                    "job_directory": str(staged.directory),
                }
            environment["PROTON_ENABLE_WAYLAND"] = "1"
            environment["PROTON_USE_WAYLAND"] = "1"
            environment["WINE_GRAPHICS_DRIVER"] = "wayland"
        else:
            raise ValueError(f"Unsupported Linux display backend: {requested_display_backend}")
    try:
        return_code, stdout, stderr = _run_bounded(
            command,
            environment=environment,
            working_directory=plan.working_directory,
            timeout_seconds=timeout_seconds,
        )
    except TimeoutError as exc:
        return {
            "schema_version": 1,
            "ok": False,
            "stage": "process-timeout",
            "error": str(exc),
            "job_id": staged.job_id,
            "job_directory": str(staged.directory),
        }

    result: dict[str, Any]
    try:
        if result_path.stat().st_size > 1024 * 1024:
            raise ValueError("carrier result exceeds 1 MiB")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("carrier result root is not an object")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        reshade_tail = _tail_text_file(staged.directory / "ReShade.log")
        process_failed = return_code != 0
        result = {
            "schema_version": 1,
            "ok": False,
            "stage": "process-crash" if process_failed else "result-read",
            "error": (
                f"Carrier exited with code {return_code} before writing its result."
                if process_failed
                else f"Carrier result could not be read: {exc}"
            ),
        }
        if reshade_tail:
            result["reshade_log_tail"] = reshade_tail
    result["job_id"] = staged.job_id
    result["job_directory"] = str(staged.directory)
    result["staging_modes"] = staged.staging_modes
    result["process"] = {
        "return_code": return_code,
        "stdout": stdout,
        "stderr": stderr,
    }
    result["graphical_session"] = {
        "requested_backend": requested_display_backend,
        "effective_backend": effective_display_backend,
        "display": environment.get("DISPLAY"),
        "wayland_display": environment.get("WAYLAND_DISPLAY"),
        "detected": detected_session,
    }
    if return_code != 0:
        result["ok"] = False
    return result
