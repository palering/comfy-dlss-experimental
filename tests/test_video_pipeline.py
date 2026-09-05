import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.media_clip import ClipRequest, file_hash, run_cancellable
from comfy_dlss_experimental.presets import RuntimePreset, resolve_component_paths
from comfy_dlss_experimental.video_pipeline import (_CACHE_LOCK, _lease_prepared_cache,
    bind_video_source, cancellable_lock, guide_settings, profile_settings,
    release_prepared_cache, snapshot_runtime, render_original)


class VideoPipelineTests(unittest.TestCase):
    def test_prepared_cache_discard_waits_for_all_consumers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = root / "prepared-clips" / ("a" * 64)
            prepared.mkdir(parents=True)
            (prepared / "color.rgba").write_bytes(b"prepared")
            with _CACHE_LOCK:
                _lease_prepared_cache(prepared, retain=False)
                _lease_prepared_cache(prepared, retain=False)
            first = release_prepared_cache(prepared, root, retain=False, success=True)
            self.assertTrue(first["deferred"])
            self.assertTrue(prepared.exists())
            second = release_prepared_cache(prepared, root, retain=False, success=True)
            self.assertTrue(second["removed"])
            self.assertFalse(prepared.exists())

    def test_prepared_cache_failure_or_concurrent_retain_wins(self):
        for failure, concurrent_keep in ((True, False), (False, True)):
            with self.subTest(failure=failure, concurrent_keep=concurrent_keep), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                prepared = root / "prepared-clips" / ("b" * 64)
                prepared.mkdir(parents=True)
                with _CACHE_LOCK:
                    _lease_prepared_cache(prepared, retain=False)
                    if concurrent_keep:
                        _lease_prepared_cache(prepared, retain=True)
                release_prepared_cache(prepared, root, retain=False, success=not failure)
                if concurrent_keep:
                    result = release_prepared_cache(prepared, root, retain=True, success=True)
                    self.assertIn("concurrent consumer", result["reason"])
                self.assertTrue(prepared.exists())

    def test_prepared_cache_cleanup_rejects_broad_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid = root / "prepared-clips"
            invalid.mkdir()
            with self.assertRaisesRegex(ValueError, "invalid prepared cache path"):
                release_prepared_cache(invalid, root, retain=False, success=True)
            external = root / "external"
            external.mkdir()
            symlink = invalid / ("c" * 64)
            symlink.symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked prepared cache"):
                release_prepared_cache(symlink, root, retain=False, success=True)

    def test_original_encode_is_cached_and_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = root / "prepared"
            prepared.mkdir()
            def encode(_prepared, _manifest, job, _cancelled):
                destination = job / "original.mp4"
                destination.write_bytes(b"encoded-adapted-original")
                return destination
            with patch("comfy_dlss_experimental.video_pipeline._render_original_uncached", side_effect=encode) as encoder:
                first = render_original(prepared, {}, root / "first", lambda: False)
                second = render_original(prepared, {}, root / "second", lambda: False)
                self.assertEqual(first.read_bytes(), second.read_bytes())
                self.assertEqual(encoder.call_count, 1)
                (prepared / "original-preview-v1" / "original.mp4").write_bytes(b"corrupt")
                with self.assertRaisesRegex(ValueError, "integrity"):
                    render_original(prepared, {}, root / "third", lambda: False)

    def test_direct_preset_requires_only_its_three_components(self):
        base = {"id": "direct", "backend": "direct_nr", "components": {"relay": "a", "worker": "b", "nvngx_dlssnr": "c"}}
        RuntimePreset.from_dict(base)
        for value in (base | {"components": {"worker": "x"}}, base | {"wine_dll_overrides": {"dxgi": "n"}}, base | {"backend": "typo"}):
            with self.assertRaises(ValueError):
                RuntimePreset.from_dict(value)

    def test_install_example_resolves_from_data_root_preset_directory(self):
        example = Path(__file__).resolve().parents[1] / "examples/runtime-presets/direct-nr.example.json"
        preset = RuntimePreset.load(example)
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            relay = data / "installed-relay.exe"
            with patch("comfy_dlss_experimental.helper_artifacts.find_helper", return_value=relay):
                paths = resolve_component_paths(preset, base_dir=data / "runtime-presets")
            expected = data / "components/nr/converter-v0.1.0-rtx40"
            self.assertEqual(paths["worker"], (expected / "nvngx.dll").resolve())
            self.assertEqual(paths["nvngx_dlssnr"], (expected / "nvngx_dlssnr.dll").resolve())
            self.assertEqual(paths["relay"], relay)

    def test_legacy_controls_and_invalid_mix_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Legacy"):
            profile_settings({"schema_version": 1}, 64, 64, 1, 120)
        with self.assertRaises(ValueError):
            profile_settings({"schema_version": 2, "mix": float("nan")}, 64, 64, 1, 120)
        with self.assertRaises(ValueError):
            guide_settings({"schema_version": 2, "motion_provider": "external"})

    def test_snapshot_is_a_copy_and_stale_descriptor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            components = {}
            for role in ("relay", "worker", "nvngx_dlssnr"):
                source = root / (role + ".bin")
                source.write_bytes(role.encode())
                components[role] = str(source)
            hashes = {role: file_hash(Path(path)) for role, path in components.items()}
            runtime = {"ready": True, "backend": "direct_nr", "components": components, "component_hashes": hashes}
            relay, worker = snapshot_runtime(runtime, root / "data")
            self.assertEqual(worker.name, "nvngx.dll")
            self.assertEqual((worker.parent / "nvngx_dlssnr.dll").read_bytes(), b"nvngx_dlssnr")
            self.assertNotEqual(worker.stat().st_ino, Path(components["worker"]).stat().st_ino)
            Path(components["worker"]).write_bytes(b"replacement")
            self.assertEqual(worker.read_bytes(), b"worker")
            with self.assertRaisesRegex(ValueError, "changed"):
                snapshot_runtime(runtime, root / "data")

    def test_cancelled_lock_is_released(self):
        lock = threading.Lock()
        with self.assertRaises(InterruptedError):
            with cancellable_lock(lock, lambda: True):
                self.fail("must not run")
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_encoder_cancel_reaps_exact_subprocess(self):
        started = time.monotonic()
        processes = []
        real = subprocess.Popen
        def launch(*args, **kwargs):
            process = real(*args, **kwargs)
            processes.append(process)
            return process
        with patch("comfy_dlss_experimental.media_clip.subprocess.Popen", side_effect=launch):
            with self.assertRaises(InterruptedError):
                run_cancellable([sys.executable, "-c", "import time; time.sleep(30)"], timeout=5,
                                cancelled=lambda: time.monotonic() - started > 0.05)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(processes[0].poll())

    def test_file_video_trim_is_not_ignored(self):
        class Video:
            def get_stream_source(self): return source
            def get_dimensions(self): return (128, 96)
            def get_active_trim_window(self): return (10, 4)
            def get_duration(self): return 4
        api = types.ModuleType("comfy_api.latest")
        api.InputImpl = types.SimpleNamespace(VideoFromFile=Video)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "video.mp4"
            source.write_bytes(b"fixture")
            with patch.dict(sys.modules, {"comfy_api.latest": api}), patch("comfy_dlss_experimental.video_pipeline.probe_media", return_value={"video": {"width": 128, "height": 96}}):
                resolved, request = bind_video_source(Video(), ClipRequest(1, 2, 5, 1), Path(temporary))
                self.assertEqual(resolved, source.resolve())
                self.assertEqual(request.start, 11)
                self.assertEqual(request.pre_roll, 1)
                self.assertEqual(request.duration, 2)

    def test_crop_uses_public_materialization_not_raw_source(self):
        calls = []
        class Video:
            def get_stream_source(self): return source
            def get_dimensions(self): return (64, 64)
            def as_trimmed(self, **kwargs): calls.append(kwargs); return self
            def save_to(self, path, **kwargs): calls.append(path); Path(path).write_bytes(b"cropped")
        api = types.ModuleType("comfy_api.latest")
        api.InputImpl = types.SimpleNamespace(VideoFromFile=Video)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            with patch.dict(sys.modules, {"comfy_api.latest": api}), patch("comfy_dlss_experimental.video_pipeline.probe_media", return_value={"video": {"width": 128, "height": 96}}):
                path, request = bind_video_source(Video(), ClipRequest(1, 2, 0.5), root)
                self.assertEqual(path.read_bytes(), b"cropped")
                self.assertEqual(calls[0], {"start_time": 0.5, "duration": 2.5})
                self.assertEqual(request.start, 0.5)
