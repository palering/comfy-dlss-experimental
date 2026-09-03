from __future__ import annotations

import json
import platform
from pathlib import Path

from comfy_api.latest import io

from ..config import data_root
from ..platform_runtime import resolve_execution, runtime_proton_choices
from ..presets import RuntimePreset, execution_fingerprint, preset_fingerprint, resolve_component_paths
from .types import RuntimeCatalog, RuntimeConfig


class DLSSExperimentalRuntimeConfig(io.ComfyNode):
    @classmethod
    def validate_inputs(cls, proton="auto"):
        # Comfy must not reject a saved Linux combo before our OS/path selection.
        # The actual launcher is validated by resolve_execution at execution time.
        return True if isinstance(proton, str) else "Proton selection must be a string"

    @staticmethod
    def _resolve_preset_path(runtime_preset: str) -> Path:
        preset_path = Path(runtime_preset).expanduser()
        return preset_path if preset_path.is_absolute() else data_root() / preset_path

    @staticmethod
    def _file_state(path: Path) -> tuple[str, int, int] | tuple[str, str]:
        try:
            info = path.stat()
            return str(path), info.st_size, info.st_mtime_ns
        except OSError as exc:
            return str(path), f"{type(exc).__name__}:{exc.errno}"

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="DLSSExperimentalRuntimeConfig",
            display_name="DLSS 5 Runtime Configuration",
            category="DLSS Experimental/Runtime",
            search_aliases=["dlss", "proton", "runtime", "dll bundle"],
            description="Binds an execution backend, exact Proton installation, runtime preset, and worker lifecycle policy.",
            inputs=[
                io.Combo.Input("backend", options=["auto", "direct_nr", "renodx_reshade", "nvidia_official"], default="auto"),
                io.Combo.Input("proton", options=runtime_proton_choices(), default="auto",
                    display_name="已安装 Proton", tooltip="Linux / Proton 执行时有效。填写自定义路径后优先使用路径。"),
                io.Combo.Input(
                    "linux_display_backend",
                    options=["auto", "xwayland", "wayland_native_experimental"],
                    default="auto",
                    tooltip="Auto currently selects the verified Xwayland route. Native Wayland is opt-in and Proton-version-specific.",
                ),
                io.Combo.Input("worker_policy", options=["isolated", "persistent", "auto"], default="isolated", advanced=True,
                               tooltip="Legacy policy input. The resident toggle takes precedence when supplied. auto = isolated."),
                io.String.Input(
                    "runtime_preset",
                    default="runtime-presets/default.json",
                    tooltip="Absolute path, or a path relative to the comfy-dlss-experimental user data directory.",
                ),
                RuntimeCatalog.Input("catalog", optional=True),
                io.Boolean.Input("keep_worker_alive", default=False, optional=True, display_name="按需启动并常驻",
                    tooltip="开启：相同尺寸及 NR 参数复用 GPU 实例；关闭：任务后立即释放。改 NR 参数会重建。"),
                io.Int.Input("idle_timeout_seconds", default=300, min=30, max=900, step=30, optional=True, advanced=True,
                    display_name="空闲自动释放（秒）", tooltip="常驻不等于永久占用。空闲到期自动释放；也可手动释放。"),
                io.Combo.Input("system_platform", options=["auto", "windows", "linux"], default="auto", optional=True,
                    display_name="系统平台", tooltip="auto 使用 ComfyUI 后端所在系统，不是浏览器系统。手动选择必须与宿主一致。"),
                io.Combo.Input("linux_execution", options=["proton", "native_reserved"], default="proton", optional=True,
                    display_name="Linux 执行方式", tooltip="proton：当前可用；native_reserved：原生 NR 预留，尚未实现。"),
                io.String.Input("proton_path", default="", optional=True, display_name="Proton 自定义路径",
                    tooltip="可填绝对安装目录或 proton 文件路径（支持 ~）；留空使用上方下拉。路径优先，不接受命令参数。"),
            ],
            outputs=[RuntimeConfig.Output("runtime"), io.String.Output("status")],
            is_experimental=True,
        )

    @classmethod
    def fingerprint_inputs(
        cls,
        backend: str,
        proton: str,
        linux_display_backend: str,
        worker_policy: str,
        runtime_preset: str,
        catalog: dict | None = None,
        keep_worker_alive: bool | None = None,
        idle_timeout_seconds: int = 300,
        system_platform: str = "auto", linux_execution: str = "proton", proton_path: str = "",
    ) -> tuple[object, ...]:
        """Invalidate Comfy's cache when a preset or selected component changes.

        Input values are already part of Comfy's normal cache key. This
        supplemental fingerprint covers files outside the workflow without
        re-hashing large vendor DLLs on every cache check; execute() performs
        the authoritative content hash whenever this metadata changes.
        """

        del backend, proton, linux_display_backend, worker_policy, catalog, keep_worker_alive, idle_timeout_seconds
        preset_path = cls._resolve_preset_path(runtime_preset)
        states: list[object] = [("preset", *cls._file_state(preset_path))]
        # Include the actual host even when a workflow carries an old catalog.
        states.append(("host", platform.system().lower()))
        if platform.system().lower() == "linux" and system_platform in {"auto", "linux"} and linux_execution == "proton" and proton_path.strip():
            path = Path(proton_path.strip()).expanduser()
            states.append(("custom-proton", *cls._file_state(path / "proton" if path.is_dir() else path)))
        try:
            preset = RuntimePreset.load(preset_path)
            for role, path in sorted(
                resolve_component_paths(preset, base_dir=preset_path.parent).items()
            ):
                states.append((role, *cls._file_state(path)))
        except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            states.append(("preset-error", type(exc).__name__, str(exc)))
        return tuple(states)

    @classmethod
    def execute(
        cls,
        backend: str,
        proton: str,
        linux_display_backend: str,
        worker_policy: str,
        runtime_preset: str,
        catalog: dict | None = None,
        keep_worker_alive: bool | None = None,
        idle_timeout_seconds: int = 300,
        system_platform: str = "auto", linux_execution: str = "proton", proton_path: str = "",
    ) -> io.NodeOutput:
        if keep_worker_alive is not None and type(keep_worker_alive) is not bool:
            raise ValueError("keep_worker_alive must be a boolean")
        if type(idle_timeout_seconds) is not int or not 30 <= idle_timeout_seconds <= 900:
            raise ValueError("idle_timeout_seconds must be 30..900")
        if worker_policy not in {"isolated", "persistent", "auto"}:
            raise ValueError("Invalid worker policy")
        if keep_worker_alive is not None:
            worker_policy = "persistent" if keep_worker_alive else "isolated"
        if worker_policy == "auto":
            worker_policy = "isolated"
        execution = resolve_execution(system_platform, linux_execution, proton, proton_path, linux_display_backend)
        system = execution["platform"]
        selected_backend = backend

        preset_path = cls._resolve_preset_path(runtime_preset)

        descriptor: dict[str, object] = {
            "schema_version": 1,
            "platform": system,
            "backend": selected_backend,
            "worker_policy": worker_policy,
            "idle_timeout_seconds": idle_timeout_seconds,
            "linux_display_backend": execution["linux_display_backend"],
            "proton": execution["proton"],
            "host_platform": execution["host_platform"],
            "execution_mode": execution["execution_mode"],
            "preset_path": str(preset_path),
            "ready": False,
            "errors": list(execution["errors"]),
        }
        errors: list[str] = descriptor["errors"]  # type: ignore[assignment]

        if selected_backend == "nvidia_official":
            errors.append("The official DLSS 5 backend adapter is reserved but not implemented yet.")
        try:
            preset = RuntimePreset.load(preset_path)
            if backend == "auto":
                selected_backend = preset.backend
                descriptor["backend"] = selected_backend
            elif preset.backend != backend:
                errors.append(f"Selected backend {backend} does not match preset {preset.backend}.")
            if selected_backend == "direct_nr":
                if linux_display_backend == "wayland_native_experimental" and system == "linux":
                    errors.append("Direct NR currently requires Xwayland; native Wayland is not verified.")
                descriptor["capabilities"] = {"preview": True, "native_resolution": True, "upscaling": False,
                                              "hdr": False, "persistent": False, "max_clip_seconds": 86400,
                                              "max_frame_count": 1_000_000, "storage_policy": "available_disk_preflight"}
            preset_key, component_hashes = preset_fingerprint(preset, base_dir=preset_path.parent)
            descriptor["preset_key"] = preset_key
            descriptor["runtime_key"] = execution_fingerprint(
                preset_key,
                platform_name=system,
                proton_selection=execution["proton"]["selection_id"] if execution["proton"] else None,
                display_backend=execution["linux_display_backend"] or "none",
                execution_mode=execution["execution_mode"],
            )
            descriptor["component_hashes"] = component_hashes
            if selected_backend == "direct_nr":
                from ..resident_worker import persistent_supported
                supported = persistent_supported(component_hashes)
                descriptor["capabilities"]["persistent"] = supported
                if worker_policy == "persistent" and not supported:
                    errors.append("这组 worker/NR DLL 尚未验证常驻重置兼容性，请关闭常驻使用立即释放模式。")
            descriptor["components"] = {
                name: str(path)
                for name, path in resolve_component_paths(preset, base_dir=preset_path.parent).items()
            }
            descriptor["wine_dll_overrides"] = preset.wine_dll_overrides
            descriptor["preset_id"] = preset.preset_id
            descriptor["compatibility"] = preset.compatibility
            missing = [name for name, value in component_hashes.items() if value.startswith("missing:")]
            if missing:
                errors.append(f"Runtime components are missing: {', '.join(missing)}")
            if selected_backend == "nvidia_official" and not any("official" in e for e in errors):
                errors.append("The official DLSS 5 backend adapter is not implemented yet.")
        except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            errors.append(f"Runtime preset could not be loaded: {exc}")

        descriptor["ready"] = not errors
        return io.NodeOutput(descriptor, json.dumps(descriptor, ensure_ascii=False, indent=2))
