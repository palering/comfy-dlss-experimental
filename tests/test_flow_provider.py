import importlib.util
import os
import struct
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
import tempfile
from unittest.mock import patch

from comfy_dlss_experimental.flow_provider import FlowProvider
from comfy_dlss_experimental.nvidia_flow import HEADER, MAGIC, PipeProcess, NvidiaFlow, helper_path, probe_nvidia, unpack_flow
from comfy_dlss_experimental.temporal_guides import GuideSettings, TemporalGuideGenerator
from comfy_dlss_experimental.media_clip import ClipRequest
from comfy_dlss_experimental.video_pipeline import guide_settings, prepared_cache

HAS_MEDIA = all(importlib.util.find_spec(name) for name in ("numpy", "cv2"))


class ProviderTests(unittest.TestCase):
    def test_old_settings_and_connected_precedence(self):
        self.assertEqual(guide_settings({"schema_version": 2}).motion_provider, "dis")
        self.assertEqual(guide_settings({"schema_version": 2, "motion_provider": "zero"}).motion_provider, "zero")
        settings = guide_settings({"schema_version": 2, "motion_provider": "zero",
                                   "flow_provider": {"schema_version": 1, "kind": "nvidia"}})
        self.assertEqual(settings.motion_provider, "nvidia")
        self.assertEqual(settings.flow, FlowProvider(kind="nvidia"))

    def test_invalid_provider(self):
        for payload in (None, {}, {"schema_version": True}, {"schema_version": 1, "kind": "invented"},
                        {"schema_version": 1, "device": True}, {"schema_version": 1, "output_grid": 3},
                        {"schema_version": 1, "temporal_hints": 1}, {"schema_version": 1, "command": "bad"}):
            with self.assertRaises(ValueError):
                FlowProvider.from_payload(payload)

    def test_mismatched_direct_settings(self):
        with self.assertRaises(ValueError):
            GuideSettings(flow=FlowProvider(kind="nvidia")).validate()

    def test_nvidia_cache_separates_parameters_and_helper_version(self):
        def prepare(_source, directory, _request, _guides, **_kwargs):
            directory.mkdir(parents=True)
            for name in ("color.rgba", "motion.rg16f"):
                (directory / name).write_bytes(bytes(64 * 64 * 4))
            return {"width": 64, "height": 64, "frames": [{}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.write_bytes(b"fixture")
            backend = {"helper_sha256": "first"}
            config = FlowProvider(kind="nvidia")
            settings = GuideSettings(motion_provider="nvidia", flow=config)
            with patch("comfy_dlss_experimental.nvidia_flow.probe_nvidia", side_effect=lambda *_args, **_kw: dict(backend)), \
                 patch("comfy_dlss_experimental.video_pipeline.probe_media", return_value={"video": {"width": 64, "height": 64}, "fps": "24"}), \
                 patch("comfy_dlss_experimental.video_pipeline.prepare_clip", side_effect=prepare):
                def cached(value):
                    return prepared_cache(source, ClipRequest(), value, root, lambda: False, lambda *_args: None)
                path, _, reused = cached(settings)
                self.assertFalse(reused)
                self.assertTrue(cached(settings)[2])
                for changed in (replace(config, output_grid=1), replace(config, preset="quality"), replace(config, temporal_hints=False)):
                    other, _, reused = cached(replace(settings, flow=changed))
                    self.assertNotEqual(path, other)
                    self.assertFalse(reused)
                backend["helper_sha256"] = "new-build"
                other, _, reused = cached(settings)
                self.assertNotEqual(path, other)
                self.assertFalse(reused)

    def test_pipe_timeout_cancel_and_reap(self):
        for cancelled, error in ((lambda: False, TimeoutError), (lambda: True, InterruptedError)):
            client = PipeProcess([sys.executable, "-c", "import time; time.sleep(10)"], cancelled, timeout=.15)
            try:
                with self.assertRaises(error):
                    client.exchange(b"", None)
            finally:
                client.close()
            self.assertIsNotNone(client.process.poll())

    def test_pipe_deadlock_free_and_bounded_stderr(self):
        program = "import sys; sys.stderr.write('x'*200000); sys.stderr.flush(); d=sys.stdin.buffer.read(200000); sys.stdout.buffer.write(d); sys.stdout.flush()"
        client = PipeProcess([sys.executable, "-c", program], timeout=5)
        try:
            self.assertEqual(client.exchange(b"a" * 200000, 200000), b"a" * 200000)
            self.assertLessEqual(len(client.stderr), 8192)
        finally:
            client.close()

    def test_pipe_reports_native_failure(self):
        client = PipeProcess([sys.executable, "-c", "import sys; sys.stderr.write('NVOF failure'); sys.exit(1)"])
        try:
            with self.assertRaisesRegex(RuntimeError, "NVOF failure"):
                client.exchange(b"", None)
        finally:
            client.close()

    def test_unavailable_platform_is_explicit(self):
        with patch("comfy_dlss_experimental.nvidia_flow.sys.platform", "darwin"):
            with self.assertRaisesRegex(RuntimeError, "Linux"):
                helper_path()

    def test_oversized_handshake_is_rejected(self):
        client = PipeProcess([sys.executable, "-c", "import sys; sys.stdout.write('x'*4096); sys.stdout.flush()"])
        try:
            with self.assertRaisesRegex(ValueError, "Oversized"):
                client.exchange(b"", None)
        finally:
            client.close()


@unittest.skipUnless(HAS_MEDIA, "requires numpy and OpenCV")
class FlowFormatTests(unittest.TestCase):
    def test_fixed_point_conversion_and_grid_not_displacement_scale(self):
        import numpy as np
        raw = struct.pack("<hhhhhhhh", -192, 128, -192, 128, -192, 128, -192, 128)
        flow = unpack_flow(raw, 2, 2, 8, 8)
        self.assertEqual(flow.shape, (8, 8, 2))
        np.testing.assert_allclose(flow[..., 0], -6)
        np.testing.assert_allclose(flow[..., 1], 4)
        with self.assertRaises(ValueError):
            unpack_flow(raw[:-1], 2, 2, 8, 8)


@unittest.skipUnless(HAS_MEDIA and os.environ.get("DLSS_TEST_NVOF") == "1", "opt-in NVIDIA hardware test")
class NvidiaHardwareTests(unittest.TestCase):
    def test_probe_translation_scales_grids_and_resets(self):
        import cv2
        import numpy as np
        report = probe_nvidia()
        self.assertEqual(report["status"], "capability_query_passed")
        rng = np.random.default_rng(731)
        gray = cv2.GaussianBlur(rng.integers(0, 256, (256, 256), dtype=np.uint8), (5, 5), 0)
        first = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGBA)
        moved = cv2.warpAffine(first, np.float32([[1, 0, 6], [0, 1, 4]]), (256, 256), borderMode=cv2.BORDER_REFLECT)
        for grid in report["grids"]:
            for scale in (.5, 1.0):
                config = FlowProvider(kind="nvidia", output_grid=grid)
                with TemporalGuideGenerator(256, 256, GuideSettings(analysis_scale=scale, motion_provider="nvidia", flow=config)) as generator:
                    pid = generator.estimator.client.process
                    zero, metrics = generator.process(first.tobytes())
                    self.assertTrue(metrics["reset"])
                    self.assertEqual(set(zero), {0})
                    result, metrics = generator.process(moved.tobytes())
                    flow = np.frombuffer(result, "<f2").reshape(256, 256, 2).astype(np.float32)
                    median = np.median(flow[40:-40, 40:-40], axis=(0, 1))
                    print("nvof", grid, scale, "median", median, "warp", metrics["warped_mae"], flush=True)
                    np.testing.assert_allclose(median, [-6, -4], atol=1)
                    self.assertLess(metrics["warped_mae"], metrics["negated_flow_mae"] * .6)
                    still, _ = generator.process(moved.tobytes())
                    np.testing.assert_allclose(np.median(np.frombuffer(still, "<f2").reshape(256, 256, 2)[40:-40, 40:-40], axis=(0, 1)), [0, 0], atol=.5)
                    zero, metrics = generator.process(first.tobytes(), force_reset=True)
                    self.assertEqual(set(zero), {0})
                    self.assertEqual(metrics["reset_reason"], "explicit")
                    self.assertTrue(generator.estimator.pending_reset)
                    generator.process(moved.tobytes())
                    self.assertFalse(generator.estimator.pending_reset)
                self.assertIsNotNone(pid.poll())

    def test_all_presets(self):
        import numpy as np
        for preset in ("fast", "balanced", "quality"):
            flow = NvidiaFlow(128, 128, FlowProvider(kind="nvidia", preset=preset, temporal_hints=False))
            try:
                before = np.zeros((128, 128), np.uint8)
                b, f = flow.estimate(before, before)
                self.assertTrue(np.isfinite(b).all() and np.isfinite(f).all())
            finally:
                flow.close()

    def test_native_invalid_arguments_and_truncated_request(self):
        helper = str(helper_path())
        for args in (["--probe", "-1"], ["--serve", "0", "64", "10", "4", "0", "1"],
                     ["--serve", "64", "64", "10", "3", "0", "1"]):
            result = subprocess.run([helper, *args], capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"invalid", result.stderr)
        command = [helper, "--serve", "128", "128", "10", "4", "0", "1"]
        for payload, message in ((HEADER.pack(MAGIC, 0, 0, 128 * 128), b"truncated request"),
                                 (HEADER.pack(MAGIC, 5, 0, 128 * 128), b"invalid frame request")):
            result = subprocess.run(command, input=payload, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn(message, result.stderr)

    def test_scene_cut_and_cancel_close_the_native_session(self):
        import numpy as np
        cancel = [False]
        with TemporalGuideGenerator(128, 128, GuideSettings(motion_provider="nvidia"), cancelled=lambda: cancel[0]) as generator:
            process = generator.estimator.client.process
            black = np.zeros((128, 128, 4), np.uint8)
            white = np.full_like(black, 255)
            generator.process(black.tobytes())
            raw, metrics = generator.process(white.tobytes())
            self.assertEqual(metrics["reset_reason"], "scene_cut")
            self.assertEqual(set(raw), {0})
            cancel[0] = True
            with self.assertRaises(InterruptedError):
                generator.process(white.tobytes())
        self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()
