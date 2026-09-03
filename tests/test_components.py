from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from comfy_dlss_experimental.components import ComponentManifest, scan_component_manifests


class ComponentManifestTests(unittest.TestCase):
    def test_scans_and_hashes_component(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "reshade/6.8"
            directory.mkdir(parents=True)
            (directory / "dxgi.dll").write_bytes(b"test")
            (directory / "component.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "id": "reshade-6.8",
                    "kind": "reshade",
                    "version": "6.8",
                    "files": {"proxy": "dxgi.dll"},
                }),
                encoding="utf-8",
            )
            components, errors = scan_component_manifests(Path(raw))
            self.assertEqual(errors, [])
            self.assertEqual(len(components), 1)
            self.assertTrue(components[0]["ready"])
            self.assertEqual(len(components[0]["files"]["proxy"]["sha256"]), 64)

    def test_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifest = Path(raw) / "component.json"
            manifest.write_text(
                json.dumps({
                    "id": "bad",
                    "kind": "dlss",
                    "version": "x",
                    "files": {"runtime": "../nvngx_dlss.dll"},
                }),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                ComponentManifest.load(manifest)
