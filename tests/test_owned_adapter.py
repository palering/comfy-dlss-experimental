from dataclasses import replace
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.direct_nr import DirectNRSettings
from comfy_dlss_experimental.owned_adapter import OwnedMediaClient, owned_settings, half_to_rgba8
from comfy_dlss_experimental.presets import RuntimePreset, resolve_component_paths
from comfy_dlss_experimental.resident_worker import ResidentWorker
from comfy_dlss_experimental.video_pipeline import snapshot_runtime
from comfy_dlss_experimental.media_clip import file_hash
from comfy_dlss_experimental.processing_plan import resolve_nr_parts


class Wire:
    def __init__(self):
        self.creates = self.ends = 0
        self.frames = []
        self.fail_end = False

    def create(self, settings, session_id):
        self.creates += 1
        self.settings = settings

    def process(self, color, motion, pts, *, reset):
        self.frames.append((color, motion, pts, reset))
        return color

    def end(self):
        if self.fail_end:
            raise RuntimeError("END failed")
        self.ends += 1


class Process:
    run_marker = "owned-adapter-test"
    def __init__(self):
        self.client = Wire()
        self.healthy = True
        self.cleanup = {}
        self.shutdowns = 0

    def close(self):
        self.healthy = False

    def shutdown(self):
        self.shutdowns += 1
        self.close()


class OwnedBindingTests(unittest.TestCase):
    def test_owned_components_are_distinct_and_snapshotted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            components = {}
            for role in ("worker", "caller", "nvngx_dlssnr"):
                p = root / (role + ".bin")
                p.write_bytes(role.encode())
                components[role] = str(p)
            preset = {"id": "owned", "backend": "owned_nr", "components": components}
            RuntimePreset.from_dict(preset)
            hashes = {k: file_hash(Path(v)) for k, v in components.items()}
            caller, worker = snapshot_runtime(dict(preset, ready=True, component_hashes=hashes), root / "data")
            self.assertEqual(caller.relative_to(worker.parent).as_posix(), "caller/nvngx.dll")
            self.assertEqual(worker.name, "comfy-dlss-worker.exe")
            self.assertEqual(caller.read_bytes(), b"caller")
            self.assertFalse((worker.parent / "dlss-native-relay.exe").exists())
            for overrides in ({"wine_dll_overrides": {"dxgi": "n"}}, {"components": {"worker": "x"}}):
                with self.assertRaises(ValueError):
                    RuntimePreset.from_dict(preset | overrides)
            Path(components["caller"]).write_bytes(b"replacement")
            with self.assertRaisesRegex(ValueError, "changed"):
                snapshot_runtime(dict(preset, ready=True, component_hashes=hashes), root / "data")

    def test_unsupported_controls_and_dimensions_rejected_before_create(self):
        session = Process()
        for settings in (DirectNRSettings(1921, 64, 1), DirectNRSettings(64, 1081, 1),
                         DirectNRSettings(64, 64, 1, profile=0), DirectNRSettings(64, 64, 1000000)):
            with self.assertRaises(ValueError):
                OwnedMediaClient(session, settings)
        self.assertEqual(session.client.creates, 0)

    def test_runtime_binding_keeps_old_backend_and_selects_owned_explicitly(self):
        look = {"schema_version": 2}
        self.assertEqual(resolve_nr_parts(look, {"backend": "owned_nr"})[1]["backend_id"], "owned_cnr1_nr")
        self.assertEqual(resolve_nr_parts(look, {"backend": "direct_nr"})[1]["backend_id"], "external_d5v2_nr")
        with self.assertRaises(ValueError):
            resolve_nr_parts(look, {"backend": "nvidia_official"})

    def test_bundled_worker_cannot_be_bound_to_legacy_wire(self):
        preset=RuntimePreset.from_dict({'id':'wrong','backend':'direct_nr',
            'components':{'relay':'relay.exe','worker':'@bundled/worker','nvngx_dlssnr':'model.dll'}})
        with self.assertRaisesRegex(ValueError,'CNR1'):
            resolve_component_paths(preset,base_dir=Path('.'))


@unittest.skipUnless(importlib.util.find_spec('numpy'), "requires existing numpy")
class OwnedMediaTests(unittest.TestCase):
    def test_numeric_roundtrip_and_timestamps_without_eotf(self):
        import numpy as np
        process = Process()
        client = OwnedMediaClient(process, DirectNRSettings(64, 64, 1, warmup=1))
        color = bytes(range(256)) * 64
        motion = bytes(64*64*4)
        self.assertEqual(client.process(color, motion, 123, reset=True), color)
        half, received_motion, pts, reset = process.client.frames[0]
        self.assertAlmostEqual(float(np.frombuffer(half, '<f2')[128]), 128/255, places=3)
        self.assertEqual((received_motion, pts, reset), (motion, 123, True))
        client.finish()
        self.assertEqual((process.client.ends, process.shutdowns), (1, 1))

    def test_nonfinite_output_fails_and_finite_out_of_range_clamps(self):
        import numpy as np
        with self.assertRaisesRegex(ValueError, "non-finite"):
            half_to_rgba8(np.array([float('nan')], '<f2').tobytes(), 1)
        self.assertEqual(half_to_rgba8(np.array([-1, 2], '<f2').tobytes(), 2), bytes([0,255]))
        with self.assertRaisesRegex(ValueError, "length"):
            half_to_rgba8(b'', 2)

    def test_resident_end_reset_reuse_release_and_failed_end(self):
        now = [0.0]
        pool = ResidentWorker(clock=lambda: now[0], background=False)
        processes = []
        def launch():
            p = Process()
            processes.append(p)
            return p
        def acquire():
            return pool.acquire(key="owned", runtime={"backend": "owned_nr"}, settings=DirectNRSettings(64,64,1),
                factory=launch, idle_seconds=30, client_factory=OwnedMediaClient)
        try:
            for index in range(2):
                lease = acquire()
                self.assertEqual(lease.reused, index == 1)
                lease.client.process(bytes(64*64*4), bytes(64*64*4), 0, reset=True)
                self.assertTrue(pool.finish(lease, complete=True)["retained"])
            self.assertEqual((len(processes), processes[0].client.creates, processes[0].client.ends), (1,1,2))
            self.assertEqual(pool.snapshot()["workers"][0]["protocol"], "CNR1")
            now[0] = 30
            pool.maintain(sample=False)
            self.assertFalse(processes[0].healthy)
            lease = acquire()
            lease.client.process(bytes(64*64*4), bytes(64*64*4), 0, reset=True)
            processes[-1].client.fail_end = True
            with self.assertRaisesRegex(RuntimeError, "END failed"):
                pool.finish(lease, complete=True)
            self.assertFalse(processes[-1].healthy)
            self.assertEqual(pool.snapshot()["workers"], [])
        finally:
            pool.shutdown()
