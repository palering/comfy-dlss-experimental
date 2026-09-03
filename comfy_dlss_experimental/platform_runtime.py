"""Host-validated execution selection; importing this module probes nothing."""
from __future__ import annotations

import platform


def resolve_execution(system_platform="auto", linux_execution="proton", proton="auto", proton_path="",
                      linux_display_backend="auto"):
    host = platform.system().lower()
    if system_platform not in {"auto", "windows", "linux"}:
        raise ValueError("Unknown system platform")
    selected = host if system_platform == "auto" else system_platform
    result = {"platform": selected, "host_platform": host, "execution_mode": None,
              "proton": None, "linux_display_backend": None, "errors": []}
    errors = result["errors"]
    if selected != host:
        errors.append(f"所选平台 {selected} 与 ComfyUI 宿主 {host} 不一致；此选项不连接远程主机。")
        return result  # Never even discover another OS's runtimes.
    if host == "windows":
        result["execution_mode"] = "windows_native"
        return result
    if host != "linux":
        errors.append("执行仅支持 Windows 或 Linux；当前宿主只能编辑工作流。")
        return result
    if linux_execution not in {"proton", "native_reserved"}:
        errors.append("Unknown Linux execution mode")
        return result
    if linux_execution == "native_reserved":
        result["execution_mode"] = "linux_native_reserved"
        errors.append("Linux 原生 NR 为预留接口，尚未实现；不会回退到 Proton 或执行 Windows 文件。")
        return result
    result["execution_mode"] = "linux_proton"
    if linux_display_backend not in {"auto", "xwayland", "wayland_native_experimental"}:
        errors.append("Unknown Linux display backend")
        return result
    result["linux_display_backend"] = linux_display_backend
    # Linux-only discovery is imported and called only after host validation.
    from .discovery import discover_proton_installations, resolve_proton_choice, custom_proton
    try:
        installation = custom_proton(proton_path) if proton_path.strip() else resolve_proton_choice(
            proton, discover_proton_installations(platform_name="linux"))
        if installation is None:
            errors.append("所选 Proton 不可用；请选择已安装版本或填写 Proton 启动文件/安装目录。")
        else:
            result["proton"] = installation.to_dict()
    except (ValueError, OSError) as exc:
        errors.append(str(exc))
    return result


def runtime_proton_choices():
    if platform.system().lower() != "linux":
        return ["auto"]
    from .discovery import proton_choices
    return proton_choices()


def validate_execution_host(runtime):
    """Recheck before launch, including descriptors made outside a node."""
    host = platform.system().lower()
    if host not in {"linux", "windows"} or runtime.get("platform") != host:
        raise ValueError("Runtime 平台与当前 ComfyUI 宿主不匹配")
    expected = "linux_proton" if host == "linux" else "windows_native"
    if runtime.get("execution_mode", expected) != expected:
        raise ValueError("所选执行方式尚未实现，不能启动 NR Worker")
    return host
