from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from comfy_dlss_experimental.presets import RuntimePreset, execution_fingerprint, preset_fingerprint


class RuntimePresetTests(unittest.TestCase):
    def test_native_compiler_override_can_use_proton_prefix_component(self) -> None:
        preset = RuntimePreset.from_dict(
            {
                "schema_version": 1,
                "id": "prefix-compiler",
                "backend": "renodx_reshade",
                "components": {
                    "sidecar": "sidecar.exe",
                    "reshade_proxy": "dxgi.dll",
                    "renodx_addon": "renodx.addon64",
                    "nvngx_dlss": "nvngx_dlss.dll",
                    "nvngx_dlssnr": "nvngx_dlssnr.dll",
                },
                "wine_dll_overrides": {"d3dcompiler_47": "n", "dxgi": "n,b"},
            }
        )
        self.assertEqual(preset.wine_dll_overrides["d3dcompiler_47"], "n")

    def test_fingerprint_changes_with_component_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            names = ["sidecar", "reshade_proxy", "renodx_addon", "nvngx_dlss", "nvngx_dlssnr"]
            components = {}
            for name in names:
                path = root / f"{name}.bin"
                path.write_bytes(name.encode())
                components[name] = path.name
            preset = RuntimePreset.from_dict(
                {"schema_version": 1, "id": "test", "backend": "renodx_reshade", "components": components}
            )
            first, _ = preset_fingerprint(preset, base_dir=root)
            (root / "nvngx_dlssnr.bin").write_bytes(b"different")
            second, _ = preset_fingerprint(preset, base_dir=root)
            self.assertNotEqual(first, second)

    def test_missing_component_is_reported_in_hash_map(self) -> None:
        preset = RuntimePreset.from_dict(
            {
                "schema_version": 1,
                "id": "test",
                "backend": "renodx_reshade",
                "components": {name: f"{name}.bin" for name in ["sidecar", "reshade_proxy", "renodx_addon", "nvngx_dlss", "nvngx_dlssnr"]},
            }
        )
        with tempfile.TemporaryDirectory() as raw:
            _, hashes = preset_fingerprint(preset, base_dir=Path(raw))
        self.assertTrue(all(value.startswith("missing:") for value in hashes.values()))

    def test_execution_fingerprint_separates_display_backends(self) -> None:
        common = {
            "platform_name": "linux",
            "proton_selection": "Proton-GE",
        }
        xwayland = execution_fingerprint("a" * 64, display_backend="xwayland", **common)
        wayland = execution_fingerprint("a" * 64, display_backend="wayland_native_experimental", **common)
        self.assertNotEqual(xwayland, wayland)
