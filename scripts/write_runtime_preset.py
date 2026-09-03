#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def existing_file(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file not found: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Write one explicit RenoDX/ReShade runtime preset.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--id", default="renodx-proton-default")
    parser.add_argument("--sidecar", type=existing_file, required=True)
    parser.add_argument("--reshade", type=existing_file, required=True)
    parser.add_argument("--renodx", type=existing_file, required=True)
    parser.add_argument("--dlss", type=existing_file, required=True)
    parser.add_argument("--dlssnr", type=existing_file, required=True)
    parser.add_argument("--d3dcompiler-47", type=existing_file)
    arguments = parser.parse_args()

    output = arguments.output.expanduser().resolve()
    if output.exists():
        parser.error(f"refusing to overwrite existing preset: {output}")
    components = {
        "sidecar": str(arguments.sidecar),
        "reshade_proxy": str(arguments.reshade),
        "renodx_addon": str(arguments.renodx),
        "nvngx_dlss": str(arguments.dlss),
        "nvngx_dlssnr": str(arguments.dlssnr),
    }
    if arguments.d3dcompiler_47:
        components["d3dcompiler_47"] = str(arguments.d3dcompiler_47)
    payload = {
        "schema_version": 1,
        "id": arguments.id,
        "backend": "renodx_reshade",
        "components": components,
        "wine_dll_overrides": {
            "d3dcompiler_47": "n",
            "dxgi": "n,b",
        },
        "compatibility": {
            "streamline": "unused",
            "carrier_contract": "d3d12-reshade-renodx-bootstrap-v1",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
