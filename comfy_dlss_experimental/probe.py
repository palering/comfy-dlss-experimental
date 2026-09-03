from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .models import ProbeReport


@dataclass(frozen=True, slots=True)
class CommandResult:
    available: bool
    returncode: int | None = None
    output: str = ""

    def to_dict(self, *, include_output: bool) -> dict[str, object]:
        result: dict[str, object] = {"available": self.available, "returncode": self.returncode}
        if include_output and self.output:
            result["output"] = self.output
        return result


def _run(command: list[str], timeout: float = 5.0) -> CommandResult:
    executable = shutil.which(command[0])
    if not executable:
        return CommandResult(available=False)
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(available=True, output=str(exc))
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return CommandResult(True, completed.returncode, output[:16_384])


def probe_environment(*, include_diagnostics: bool = False) -> ProbeReport:
    system = platform.system().lower()
    protons = []
    if system == "linux":
        from .discovery import discover_proton_installations
        protons = discover_proton_installations(platform_name=system)
    report = ProbeReport(
        platform=system,
        architecture=platform.machine(),
        supported_host=system in {"linux", "windows"},
        python_version=sys.version.split()[0],
        protons=protons,
    )

    ffmpeg = _run(["ffmpeg", "-version"])
    ffprobe = _run(["ffprobe", "-version"])
    nvidia = _run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,driver_version,memory.total",
            "--format=csv,noheader",
        ]
    )
    report.tools = {
        "ffmpeg": ffmpeg.to_dict(include_output=include_diagnostics),
        "ffprobe": ffprobe.to_dict(include_output=include_diagnostics),
    }
    report.gpu = {"nvidia_smi": nvidia.to_dict(include_output=True)}

    if system == "linux":
        protonplus = _run(["protonplus", "version"])
        vulkan = _run(["vulkaninfo", "--summary"], timeout=10.0)
        report.tools["protonplus"] = protonplus.to_dict(include_output=True)
        report.tools["vulkan"] = vulkan.to_dict(include_output=include_diagnostics)
        if not protons:
            report.warnings.append("No executable Proton installation was found. Install one with Steam/ProtonPlus or configure COMFY_DLSS_PROTON_PATHS.")
    elif system == "windows":
        report.tools["execution"] = {"mode": "native"}
    else:
        report.warnings.append("This host is for development only. DLSS execution is supported on Windows or Linux through Proton.")

    if not nvidia.available or nvidia.returncode != 0:
        report.warnings.append("NVIDIA GPU/driver detection failed.")
    if not ffmpeg.available or not ffprobe.available:
        report.warnings.append("FFmpeg and ffprobe are required for video jobs.")
    return report
