#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comfy_dlss_experimental.jobs import run_carrier_bootstrap
from comfy_dlss_experimental.models import ProtonInstallation
from comfy_dlss_experimental.presets import RuntimePreset, execution_fingerprint, preset_fingerprint


def existing_file(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file not found: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the carrier bootstrap from one runtime preset.")
    parser.add_argument("--preset", type=existing_file, required=True)
    parser.add_argument("--proton", type=existing_file)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--presents", type=int, default=180)
    parser.add_argument("--ngx-smoke", action="store_true")
    parser.add_argument("--ngx-evaluations", type=int, default=30)
    parser.add_argument(
        "--display-backend",
        choices=("auto", "xwayland", "wayland_native_experimental"),
        default="auto",
    )
    arguments = parser.parse_args()

    preset = RuntimePreset.load(arguments.preset)
    preset_key, _ = preset_fingerprint(preset, base_dir=arguments.preset.parent)
    proton = None
    platform_name = "windows"
    if arguments.proton:
        platform_name = "linux"
        installation = ProtonInstallation(
            source="explicit-cli",
            name=arguments.proton.parent.name,
            version="explicit",
            root=arguments.proton.parent,
            executable=arguments.proton,
        )
        proton = installation.to_dict()
    runtime_key = execution_fingerprint(
        preset_key,
        platform_name=platform_name,
        proton_selection=proton["selection_id"] if proton else None,
        display_backend=arguments.display_backend,
    )
    runtime = {
        "schema_version": 1,
        "platform": platform_name,
        "backend": preset.backend,
        "worker_policy": "isolated",
        "linux_display_backend": arguments.display_backend,
        "proton": proton,
        "preset_path": str(arguments.preset),
        "preset_key": preset_key,
        "runtime_key": runtime_key,
        "ready": True,
        "errors": [],
    }
    result = run_carrier_bootstrap(
        runtime=runtime,
        data_root=arguments.data_root.expanduser().resolve(),
        presents=arguments.presents,
        ngx_smoke=arguments.ngx_smoke,
        ngx_evaluations=arguments.ngx_evaluations,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
