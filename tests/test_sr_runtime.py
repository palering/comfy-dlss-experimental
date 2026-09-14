from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from comfy_dlss_experimental.sr_runtime import project_id, snapshot_sr, validate_sr_runtime


class SRRuntimeTests(unittest.TestCase):
    def setUp(self):
        fixture_root = Path(__file__).resolve().parents[1] / 'tmp'
        fixture_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=fixture_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = {"backend": "owned_sr", "ready": True, "worker_policy": "isolated",
                        "runtime_key": "a" * 64, "compatibility": {"project_id": "f11d0944-2512-4ea1-a5a7-f7ee4987f3f5"},
                        "components": {}, "component_hashes": {}}
        for role in ("worker", "nvngx_dlss"):
            data = b"MZ" + role.encode()
            path = self.root / role
            path.write_bytes(data)
            self.runtime["components"][role] = str(path)
            self.runtime["component_hashes"][role] = hashlib.sha256(data).hexdigest()

    def test_project_identity_is_explicit_and_canonical(self):
        value = self.runtime["compatibility"]["project_id"]
        self.assertEqual(project_id(value), value)
        for invalid in (None, "", value.upper(), value.replace("-", ""), "0" * 32,
                        "00000000-0000-0000-0000-000000000000", {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): project_id(invalid)

    def test_preset_requires_explicit_sdk_worker_and_rejects_nr_bundle_alias(self):
        from comfy_dlss_experimental.presets import RuntimePreset, resolve_component_paths
        preset = RuntimePreset.from_dict({"schema_version": 1, "id": "sr-test", "backend": "owned_sr",
            "components": {"worker": "@bundled/worker", "nvngx_dlss": "nvngx_dlss.dll"},
            "compatibility": self.runtime["compatibility"]})
        with self.assertRaisesRegex(ValueError, "explicit SDK-enabled Worker"):
            resolve_component_paths(preset, base_dir=self.root)
        preset.components["worker"] = "comfy-dlss-worker.exe"
        self.assertEqual(resolve_component_paths(preset, base_dir=self.root)["worker"], self.root / "comfy-dlss-worker.exe")

    def test_exact_roles_runtime_and_isolated_mode(self):
        validate_sr_runtime(self.runtime)
        for change in ({"backend": "owned_nr"}, {"ready": False}, {"worker_policy": "persistent"},
                       {"components": {"worker": "x", "caller_shim": "y", "nvngx_dlssnr": "z"}},
                       {"runtime_key": "../bad"}, {"compatibility": {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_sr_runtime(self.runtime | change)

    def test_snapshot_reuses_two_verified_files_without_nr_shim(self):
        worker, model = snapshot_sr(self.runtime, self.root)
        self.assertEqual(worker.name, "comfy-dlss-worker.exe")
        self.assertEqual(model.name, "nvngx_dlss.dll")
        self.assertEqual({p.name for p in worker.parent.iterdir()}, {worker.name, model.name})
        times = worker.stat().st_mtime_ns, model.stat().st_mtime_ns
        self.assertEqual(snapshot_sr(self.runtime, self.root), (worker, model))
        self.assertEqual(times, (worker.stat().st_mtime_ns, model.stat().st_mtime_ns))

    def test_stale_source_and_tampered_snapshot_are_not_replaced(self):
        worker, model = snapshot_sr(self.runtime, self.root)
        worker.write_bytes(b"MZtampered")
        with self.assertRaisesRegex(ValueError, "snapshot.*changed"):
            snapshot_sr(self.runtime, self.root)
        self.assertEqual(worker.read_bytes(), b"MZtampered")
        Path(self.runtime["components"]["nvngx_dlss"]).write_bytes(b"MZnew-model")
        with self.assertRaisesRegex(ValueError, "changed after Runtime"):
            snapshot_sr(self.runtime, self.root)

    def test_symlinked_snapshot_and_non_pe_component_fail(self):
        worker, model = snapshot_sr(self.runtime, self.root)
        worker.unlink()
        worker.symlink_to(self.runtime["components"]["worker"])
        with self.assertRaisesRegex(ValueError, "snapshot"):
            snapshot_sr(self.runtime, self.root)
        runtime = deepcopy(self.runtime)
        source = Path(runtime["components"]["worker"])
        source.write_bytes(b"not-a-pe")
        runtime["component_hashes"]["worker"] = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "Windows PE"):
            snapshot_sr(runtime, self.root)


if __name__ == "__main__":
    unittest.main()
