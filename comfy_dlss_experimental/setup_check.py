"""Read-only validation for one selected DLSS execution configuration."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import platform
from typing import Any

from .flow_provider import FlowProvider
from .media_tools import resolve_media_tools
from .probe import probe_environment


KNOWN_RUNTIME_PAIRS = {
    (
        "99ef1f2976d9cd16b7fc269adb6c6450fb64c81a522c9b9e6edc6a28201dc904",
        "28bdc080d28686decdb63f6f4246b022274916b80aafdab266fe0fb63b2b9265",
    ): "DLSS5-Video-Converter v0.1.0 RTX40 pair tested by this project",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package(module: str, distribution: str | None = None, *, import_module: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"module": module, "available": importlib.util.find_spec(module) is not None}
    if not result["available"]:
        return result
    try:
        result["version"] = importlib.metadata.version(distribution or module)
    except importlib.metadata.PackageNotFoundError:
        result["version"] = None
    if import_module:
        try:
            result["loaded_version"] = getattr(importlib.import_module(module), "__version__", None)
        except (ImportError, OSError) as exc:
            result.update(available=False, error=str(exc))
    return result


def _selected_flow(sequence: dict | None, flow_provider: dict | None) -> tuple[FlowProvider | None, list[str]]:
    warnings: list[str] = []
    sequence_value = None
    if isinstance(sequence, dict):
        settings = sequence.get("settings")
        if isinstance(settings, dict):
            connected = settings.get("flow_provider")
            if connected is not None:
                sequence_value = FlowProvider.from_payload(connected)
            elif settings.get("motion_provider") in {"dis", "nvidia"}:
                sequence_value = FlowProvider(kind=settings["motion_provider"])
            elif settings.get("motion_provider") == "zero":
                return None, warnings
            elif settings.get("motion_provider") == "external":
                from .external_guides import reopen_external_guide
                external = reopen_external_guide(settings.get("external_motion"))
                external.validate_nr_motion()
                return external, warnings
    explicit = FlowProvider.from_payload(flow_provider) if flow_provider is not None else None
    if sequence_value and explicit and sequence_value != explicit:
        warnings.append("The separately connected flow provider differs from the Video Input Adapter settings; the video sequence wins.")
    return sequence_value or explicit or FlowProvider(), warnings


def inspect_setup(
    runtime: dict,
    *,
    sequence: dict | None = None,
    flow_provider: dict | None = None,
    verify_nvidia_flow: bool = False,
    include_diagnostics: bool = False,
) -> dict[str, Any]:
    """Inspect files and host dependencies without launching NR/SR Workers/models.

    A successful SR report establishes file/host readiness only. The CSR1 SDK
    capability handshake, actual input planes and GPU support remain untested.
    """

    from datetime import datetime, timezone

    checks: list[dict[str, Any]] = []

    def add(check_id: str, label: str, status: str, detail: str, **extra: Any) -> None:
        checks.append({"id": check_id, "label": label, "status": status, "detail": detail, **extra})

    report: dict[str, Any] = {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "read_only_preflight",
        "worker_started": False,
        "checks": checks,
    }
    if not isinstance(runtime, dict):
        add("runtime", "Runtime configuration / 运行时配置", "fail", "Connect a Runtime Configuration output.")
        report.update(ready=False, summary={"pass": 0, "warn": 0, "fail": 1})
        return report

    host = platform.system().lower()
    selected = runtime.get("platform")
    error_value = runtime.get("errors", [])
    runtime_errors = [str(value) for value in error_value] if isinstance(error_value, list) else [str(error_value)]
    if runtime.get("ready") and not runtime_errors:
        add("runtime", "Runtime configuration / 运行时配置", "pass", f"Preset {runtime.get('preset_id', 'unknown')} is internally consistent.")
    else:
        add("runtime", "Runtime configuration / 运行时配置", "fail", "; ".join(runtime_errors) or "Runtime Configuration reported not ready.")
    add("host", "Host platform / 宿主平台", "pass" if selected == host and host in {"linux", "windows"} else "fail",
        f"selected={selected}; actual={host}")
    backend = runtime.get("backend")
    owned = backend in {"owned_nr", "owned_sr", "owned_sl"}
    is_sr = backend == "owned_sr"
    is_sl = backend == "owned_sl"
    descriptions = {
        "direct_nr": "direct_nr: external video Worker, D5V2.",
        "owned_nr": "owned_nr: project Worker + thin caller, CNR1; experimental, GPU acceptance is separate.",
        "owned_sr": "owned_sr: SDK-built project Worker + nvngx_dlss.dll, CSR1; no NR caller shim or NR model is used.",
        "owned_sl": "owned_sl: separate CXR1 reconstruction Worker + complete Streamline SR/RR runtime; explicit renderer bundle inputs.",
    }
    if backend in descriptions:
        add("backend", "Execution backend / 执行后端", "pass", descriptions[backend])
    else:
        add("backend", "Execution backend / 执行后端", "fail", f"{runtime.get('backend')} is not implemented for Preview/Process Video.")
    expected_mode = {"linux": "linux_proton", "windows": "windows_native"}.get(host)
    actual_mode = runtime.get("execution_mode")
    add("execution_mode", "Execution mode / 执行方式", "pass" if expected_mode and actual_mode == expected_mode else "fail",
        f"selected={actual_mode}; expected={expected_mode or 'unsupported host'}")

    components = runtime.get("components") if isinstance(runtime.get("components"), dict) else {}
    expected_hashes = runtime.get("component_hashes") if isinstance(runtime.get("component_hashes"), dict) else {}
    from .sl_runtime import SL_COMPONENTS
    required_roles = (set(SL_COMPONENTS) if is_sl else {"worker", "nvngx_dlss"} if is_sr else
                      {"caller" if owned else "relay", "worker", "nvngx_dlssnr"})
    actual_roles = set(components)
    add("component_roles", "Component roles / 组件角色", "pass" if actual_roles == required_roles else "fail",
        f"selected={', '.join(sorted(actual_roles)) or 'none'}; required={', '.join(sorted(required_roles))}")
    component_report: dict[str, Any] = {}
    for role in sorted(required_roles):
        raw = components.get(role)
        item: dict[str, Any] = {"path": raw, "present": False}
        component_report[role] = item
        try:
            if not isinstance(raw, str) or not raw:
                raise FileNotFoundError("path is not configured")
            path = Path(raw).expanduser().resolve(strict=True)
            if not path.is_file():
                raise FileNotFoundError("not a regular file")
            info = path.stat()
            with path.open("rb") as stream:
                magic = stream.read(2)
            actual = _sha256(path)
            expected = expected_hashes.get(role)
            item.update(path=str(path), present=True, size=info.st_size, sha256=actual,
                        expected_sha256=expected, sha256_match=actual == expected, pe_image=magic == b"MZ")
            if magic != b"MZ":
                raise ValueError("file is not a Windows PE image")
            if actual != expected:
                raise ValueError("file hash changed after Runtime Configuration ran")
            add(f"component_{role}", f"Component {role}", "pass", f"{path.name}; {info.st_size:,} bytes; SHA-256 matches")
        except (OSError, ValueError) as exc:
            item["error"] = str(exc)
            add(f"component_{role}", f"Component {role}", "fail", f"{raw or 'not configured'}: {exc}")
    report["components"] = component_report
    pair_key = tuple(
        str(component_report.get(role, {}).get("sha256", ""))
        if component_report.get(role, {}).get("sha256_match") else ""
        for role in ("worker", "nvngx_dlssnr")
    )
    pair_name = None if owned else KNOWN_RUNTIME_PAIRS.get(pair_key)
    if pair_name:
        add("runtime_pair", "Worker/model pair / Worker 与模型配对", "pass", pair_name)
    elif components:
        add("runtime_pair", "Worker/model pair / Worker 与模型配对", "warn",
            "Files are present and hash-stable, but this exact pair is not in the project's validated-pair registry.")
    report["runtime_pair"] = pair_name or "unrecognized"

    if is_sr:
        from .sr_runtime import project_id, validate_sr_runtime

        report["readiness_scope"] = "files_and_host_dependencies_only"
        report["sr_validation"] = {
            "protocol": "CSR1", "sdk_compiled": "not_checked",
            "input_planes": "not_checked", "gpu": "not_executed",
            "runtime_pair": "not_validated", "execution_readiness": "not_established",
        }
        policy = runtime.get("worker_policy", "isolated")
        add("sr_worker_policy", "SR Worker lifecycle / SR Worker 生命周期",
            "pass" if policy == "isolated" else "fail",
            "SR uses an isolated Worker released after each task." if policy == "isolated"
            else "SR requires isolated Worker mode; disable keep_worker_alive.")
        compatibility = runtime.get("compatibility")
        try:
            value = project_id(compatibility.get("project_id") if isinstance(compatibility, dict) else None)
            add("sr_project_id", "SR NGX project UUID / SR NGX 项目 UUID", "pass",
                f"Canonical nonzero project UUID configured: {value}")
        except ValueError as exc:
            add("sr_project_id", "SR NGX project UUID / SR NGX 项目 UUID", "fail", str(exc))
        try:
            # Revalidate the execution contract rather than trusting a caller's
            # ready flag. Malformed compatibility/roles are handled above.
            validate_sr_runtime(runtime)
            add("sr_runtime_contract", "SR runtime contract / SR 运行时约定", "pass",
                "SR role set, isolated lifecycle, project UUID and runtime fingerprint are consistent.")
        except (ValueError, TypeError, AttributeError) as exc:
            add("sr_runtime_contract", "SR runtime contract / SR 运行时约定", "fail", str(exc))
        add("sr_capabilities", "SR compiled/GPU capability / SR 编译与 GPU 能力", "warn",
            "PE files and SHA-256 matches do not prove SR support. Execution must complete a CSR1 handshake "
            "confirming SDK-compiled SR/DLAA capabilities, then check driver/runtime support and NGX optimal settings. "
            "This helper does not start a Worker, load NGX or run the GPU.")
        add("sr_inputs", "SR frame inputs / SR 帧输入", "warn",
            "This dependency check does not validate SR depth planes, timestamps, projection calibration or color conversion. "
            "Use SR Input Check and SR Render validation; optical flow alone is not sufficient for SR.")

    if is_sl:
        from .sl_runtime import validate_sl_runtime
        report["readiness_scope"] = "files_and_host_dependencies_only"
        report["sl_validation"] = {"protocol": "CXR1", "compiled_features": "not_checked",
            "input_bundle": "not_checked", "gpu": "not_executed", "foreground_required": False}
        try:
            validate_sl_runtime(runtime)
            add("sl_runtime_contract", "Reconstruction runtime / 重建运行库", "pass",
                "Explicit CXR1 roles, project UUID and isolated lifecycle are consistent.")
        except (ValueError, TypeError, AttributeError) as exc:
            add("sl_runtime_contract", "Reconstruction runtime / 重建运行库", "fail", str(exc))
        add("sl_capabilities", "Reconstruction execution / 重建执行", "warn",
            "Files/host readiness is not GPU acceptance. Reconstruction Render must complete the CXR1 handshake "
            "and actual SR/DLAA/RR initialization/evaluation. No Worker is launched by this helper.")

    tools_config = {}
    if isinstance(sequence, dict) and isinstance(sequence.get("media_tools_config"), dict):
        tools_config = sequence["media_tools_config"]
    media = resolve_media_tools(tools_config)
    report["media_tools"] = media
    for name in ("ffmpeg", "ffprobe"):
        item = media[name]
        add(name, name, "pass" if item["available"] else "fail",
            f"{item.get('version', item.get('error', 'unavailable'))}; source={item.get('source')}", path=item.get("path"))

    packages = {"numpy": _package("numpy")} if is_sl else {
        "numpy": _package("numpy"),
        "av": _package("av"),
        "PIL": _package("PIL", "Pillow", import_module=True),
        "cv2": _package("cv2", "opencv-python", import_module=True),
    }
    try:
        cv2 = importlib.import_module("cv2") if not is_sl and packages["cv2"]["available"] else None
        if not is_sl:
            packages["cv2"]["dis_available"] = bool(cv2 and hasattr(cv2, "DISOpticalFlow_create"))
    except (ImportError, OSError) as exc:
        packages["cv2"].update(available=False, dis_available=False, error=str(exc))
    report["python_packages"] = packages
    for name, item in packages.items():
        add(f"python_{name}", f"Python {name}", "pass" if item["available"] else "fail",
            str(item.get("version") or item.get("loaded_version") or item.get("error") or "installed"))

    try:
        flow, flow_warnings = (None, []) if is_sl else _selected_flow(sequence, flow_provider)
        for warning in flow_warnings:
            add("flow_mismatch", "Optical flow / 光流", "warn", warning)
        if is_sl:
            report["flow"] = {"kind": "renderer_bundle", "estimated": False}
            add("flow", "Renderer motion / 渲染运动场", "pass",
                "CXR1 bundle supplies explicit numerical motion; no DIS or NVOF estimator is required or launched.")
        elif flow is None:
            report["flow"] = {"kind": "zero"}
            add("flow", "Optical flow / 光流", "warn", "Zero-vector diagnostic mode selected; no optical-flow dependency is used.")
        elif hasattr(flow, "cache_identity"):
            report["flow"] = {**flow.report(), "kind": "external"}
            add("flow", "External motion / 外部运动场", "pass",
                "Manifest identity and motion semantics validated; frame hashes and source PTS are checked during rendering. No estimator is launched.")
        elif flow.kind == "dis":
            report["flow"] = {"kind": "dis", "preset": flow.preset}
            available = bool(packages["cv2"].get("dis_available"))
            add("flow", "Optical flow / 光流", "pass" if available else "fail",
                "OpenCV DIS is available." if available else "Selected DIS flow requires an OpenCV build with DISOpticalFlow_create.")
        else:
            from .helper_artifacts import find_helper
            report["flow"] = {"kind": "nvidia", "preset": flow.preset, "device": flow.device}
            try:
                helper = find_helper("nvof")
                report["flow"]["helper"] = str(helper)
                add("flow_helper", "NVIDIA flow helper / NVIDIA 光流辅助程序", "pass", str(helper))
                if verify_nvidia_flow:
                    from .nvidia_flow import probe_nvidia
                    capability = probe_nvidia(flow.device)
                    report["flow"]["capability"] = capability
                    add("flow_capability", "NVIDIA flow capability / NVIDIA 光流能力", "pass",
                        f"device={flow.device}; grids={capability.get('grids')}")
                else:
                    add("flow_capability", "NVIDIA flow capability / NVIDIA 光流能力", "warn",
                        "Helper file is installed; enable the advanced capability check to load the driver API and query this GPU.")
            except (OSError, RuntimeError, ValueError) as exc:
                add("flow_helper", "NVIDIA flow helper / NVIDIA 光流辅助程序", "fail", str(exc))
    except (ValueError, TypeError) as exc:
        report["flow"] = {"error": str(exc)}
        add("flow", "Optical flow / 光流", "fail", str(exc))

    environment = probe_environment(include_diagnostics=include_diagnostics).to_dict()
    report["environment"] = environment
    gpu = environment.get("gpu", {}).get("nvidia_smi", {})
    add("nvidia_driver", "NVIDIA GPU and driver / NVIDIA GPU 与驱动",
        "pass" if gpu.get("available") and gpu.get("returncode") == 0 else "fail",
        str(gpu.get("output") or "nvidia-smi was not available or failed")[:4096])
    if host == "linux":
        proton = runtime.get("proton") if isinstance(runtime.get("proton"), dict) else {}
        executable = proton.get("executable")
        usable = bool(executable and Path(executable).is_file() and os.access(executable, os.X_OK))
        add("proton", "Selected Proton / 已选 Proton", "pass" if usable else "fail", str(executable or "not selected"))
        from .jobs import augment_graphical_session
        graphics, detected = augment_graphical_session(os.environ)
        display = graphics.get("DISPLAY")
        add("xwayland", "Xwayland display / Xwayland 显示", "pass" if display else "fail",
            f"DISPLAY={display or 'missing'}; source={detected.get('DISPLAY', 'environment')}")
        from .discovery import _steam_bases
        steam = next((path for path in _steam_bases(Path.home()) if path.is_dir()), None)
        add("steam", "Steam client directory / Steam 客户端目录", "pass" if steam else "fail", str(steam or "not found"))
    elif host == "windows":
        add("native_execution", "Native Windows execution / Windows 原生执行", "pass", "Proton and Xwayland are not used.")

    if is_sl:
        add("sequence", "Renderer bundle / 渲染数据包", "warn",
            "Use Reconstruction Bundle Input for camera/depth/material metadata, then Reconstruction Render. "
            "This helper does not validate bundle pixels, geometry or timing; ordinary VIDEO/NR guides are not substitutes.")
    elif sequence is None:
        add("sequence", "Video input configuration / 视频输入配置", "warn",
            "Video Input Adapter is not connected; PATH/default DIS were checked, but its custom media paths and selected guide settings were not.")
    else:
        input_report = sequence.get("input_report") if isinstance(sequence, dict) else None
        input_ready = bool(isinstance(input_report, dict) and input_report.get("ready"))
        add("sequence", "Video input configuration / 视频输入配置", "pass" if input_ready else "fail",
            "Connected Video Input Adapter is ready." if input_ready else "Connected Video Input Adapter has unresolved input issues.")

    summary = {status: sum(item["status"] == status for item in checks) for status in ("pass", "warn", "fail")}
    report["summary"] = summary
    report["ready"] = summary["fail"] == 0
    return report
