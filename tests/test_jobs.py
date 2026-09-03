from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from comfy_dlss_experimental.jobs import ROLE_TARGETS, stage_runtime_job
from comfy_dlss_experimental.presets import RuntimePreset


class RuntimeJobTests(unittest.TestCase):
    def _preset(self, root: Path) -> RuntimePreset:
        components: dict[str, str] = {}
        for role in ("sidecar", "reshade_proxy", "renodx_addon", "nvngx_dlss", "nvngx_dlssnr"):
            path = root / f"{role}.bin"
            path.write_bytes(role.encode("ascii"))
            components[role] = str(path)
        return RuntimePreset.from_dict(
            {
                "schema_version": 1,
                "id": "test-runtime",
                "backend": "renodx_reshade",
                "components": components,
                "wine_dll_overrides": {"d3dcompiler_47": "n", "dxgi": "n,b"},
            }
        )

    def test_stages_canonical_names_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preset = self._preset(root)
            job = stage_runtime_job(
                preset,
                preset_directory=root,
                jobs_root=root / "jobs",
                runtime_key="a" * 64,
                job_id="job-1",
            )
            for role, target_name in ROLE_TARGETS.items():
                if role in preset.components:
                    self.assertEqual(job.files[role].name, target_name)
                    self.assertEqual(job.files[role].read_bytes(), role.encode("ascii"))
            metadata = json.loads((job.directory / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["runtime_key"], "a" * 64)
            self.assertEqual(set(metadata["components"]), set(preset.components))
            self.assertEqual(job.staging_modes["sidecar"], "copy")

    def test_refuses_to_reuse_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preset = self._preset(root)
            arguments = {
                "preset_directory": root,
                "jobs_root": root / "jobs",
                "runtime_key": "b" * 64,
                "job_id": "same-job",
            }
            stage_runtime_job(preset, **arguments)
            with self.assertRaises(FileExistsError):
                stage_runtime_job(preset, **arguments)


if __name__ == "__main__":
    unittest.main()
