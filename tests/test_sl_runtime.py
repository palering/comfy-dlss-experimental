from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from comfy_dlss_experimental.presets import RuntimePreset, resolve_component_paths
from comfy_dlss_experimental.sl_runtime import SL_COMPONENTS, snapshot_sl, validate_sl_runtime


def make_runtime(root):
    root.mkdir(parents=True, exist_ok=True)
    runtime = {"ready": True, "backend": "owned_sl", "worker_policy": "isolated", "runtime_key": "d" * 64,
        "compatibility": {"project_id": "f11d0944-2512-4ea1-a5a7-f7ee4987f3f5"}, "components": {}, "component_hashes": {}}
    for role, name in SL_COMPONENTS.items():
        data = b"MZtest-only-" + role.encode()
        path = root / name
        path.write_bytes(data)
        runtime["components"][role] = str(path)
        runtime["component_hashes"][role] = hashlib.sha256(data).hexdigest()
    return runtime


class SLRuntimeTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "tmp" / "sl-runtime-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = make_runtime(self.root / "files")

    def test_explicit_preset_and_complete_snapshot(self):
        preset = RuntimePreset.from_dict({"id": "sl-test", "backend": "owned_sl",
            "components": self.runtime["components"], "compatibility": self.runtime["compatibility"]})
        self.assertEqual(set(resolve_component_paths(preset, base_dir=self.root)), set(SL_COMPONENTS))
        worker, folder = snapshot_sl(self.runtime, self.root)
        self.assertEqual(worker.name, "comfy-dlss-sl-worker.exe")
        self.assertEqual({p.name for p in folder.iterdir()}, set(SL_COMPONENTS.values()))
        modified = worker.stat().st_mtime_ns
        self.assertEqual(snapshot_sl(self.runtime, self.root), (worker, folder))
        self.assertEqual(worker.stat().st_mtime_ns, modified)

    def test_alias_wrong_protocol_missing_dll_and_persistent_rejected(self):
        for change in ({"backend": "owned_sr"}, {"ready": False}, {"worker_policy": "persistent"},
                       {"compatibility": {}}, {"runtime_key": "../bad"}, {"components": {}}):
            with self.assertRaises(ValueError):
                validate_sl_runtime(self.runtime | change)
        preset = RuntimePreset.from_dict({"id": "sl-test", "backend": "owned_sl",
            "components": self.runtime["components"] | {"worker": "@bundled/worker"}, "compatibility": self.runtime["compatibility"]})
        with self.assertRaisesRegex(ValueError, "CXR1 Worker"):
            resolve_component_paths(preset, base_dir=self.root)

    def test_source_change_and_tampered_snapshot_never_overwritten(self):
        worker, _folder = snapshot_sl(self.runtime, self.root)
        worker.write_bytes(b"MZtampered")
        with self.assertRaisesRegex(ValueError, "snapshot.*changed"):
            snapshot_sl(self.runtime, self.root)
        self.assertEqual(worker.read_bytes(), b"MZtampered")
        Path(self.runtime["components"]["nvngx_dlssd"]).write_bytes(b"MZnew")
        with self.assertRaisesRegex(ValueError, "changed after Runtime"):
            snapshot_sl(self.runtime, self.root)

    def test_non_pe_and_symlinked_snapshot_fail(self):
        worker, _folder = snapshot_sl(self.runtime, self.root)
        worker.unlink()
        worker.symlink_to(self.runtime["components"]["worker"])
        with self.assertRaisesRegex(ValueError, "snapshot"):
            snapshot_sl(self.runtime, self.root)
        runtime = deepcopy(self.runtime)
        path = Path(runtime["components"]["worker"])
        path.write_bytes(b"not PE")
        runtime["component_hashes"]["worker"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "Windows PE"):
            snapshot_sl(runtime, self.root)
