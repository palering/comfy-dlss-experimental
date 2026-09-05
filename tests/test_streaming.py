from dataclasses import asdict
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from comfy_dlss_experimental.media_clip import ClipRequest, prepare_clip, probe_media
from comfy_dlss_experimental.input_policy import InputColorPolicy
from comfy_dlss_experimental.temporal_guides import GuideSettings
from comfy_dlss_experimental.stream_media import inspect_stream, prepared_frames, StreamEncoder
from comfy_dlss_experimental.stream_render import render_stream
from comfy_dlss_experimental.nr_effect import build_pass_stack


class StreamingContractTests(unittest.TestCase):
    def test_failed_or_cancelled_stack_closes_all_workers_and_input(self):
        for error in (RuntimeError("worker failed"), InterruptedError("cancelled")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sessions = []
                closed = []
                def launch(*args, **kwargs):
                    session = MagicMock(run_marker="test", cleanup={})
                    sessions.append(session)
                    return session
                def frames(*_):
                    try:
                        yield bytes(64 * 64 * 4), bytes(64 * 64 * 4), {"pts_ns": 0, "visible": True, "reset": True}
                    finally:
                        closed.append(True)
                manifest = {"width": 64, "height": 64, "frames": [{}], "visible_count": 1,
                            "guide_settings": asdict(GuideSettings()), "color_pipeline": {}}
                look = {"schema_version": 2, "nr_enabled": True, "mix": 1.0}
                with patch("comfy_dlss_experimental.stream_render.NativeRelay", side_effect=launch), \
                        patch("comfy_dlss_experimental.stream_render.DirectNRClient") as client, \
                        patch("comfy_dlss_experimental.stream_render.StreamEncoder") as encoder, \
                        patch("comfy_dlss_experimental.stream_render.prepared_frames", side_effect=frames), \
                        patch("comfy_dlss_experimental.video_pipeline.snapshot_runtime", return_value=(root, root)), \
                        patch("comfy_dlss_experimental.video_pipeline.relay_environment", return_value=(None, {})):
                    client.return_value.process.side_effect = error
                    with self.assertRaises(type(error)):
                        render_stream(manifest, {"runtime_key": "a" * 64}, build_pass_stack(2, look),
                                      root, root / "job", 1, lambda: False, lambda *_: None)
                    encoder.return_value.__exit__.assert_called_once()
                self.assertEqual(len(sessions), 2)
                for session in sessions:
                    session.close.assert_called_once()
                self.assertEqual(closed, [True])
                self.assertFalse((root / "job/report.json").exists())

    def test_stack_uses_independent_continuous_histories_and_original_motion(self):
        calls, sessions = [], []
        class Session:
            run_marker = "test"
            def __init__(self, *args, **kwargs):
                self.index = len(sessions)
                self.cleanup = {}
                self.closed = False
                sessions.append(self)
            def close(self): self.closed = True
            def cancel(self): pass
        class Client:
            def __init__(self, session, settings): self.session = session
            def process(self, color, motion, pts, reset=False):
                calls.append((self.session.index, color[0], motion[0], pts, reset))
                return bytes([color[0] + 1]) * len(color)
            def finish(self): return {"exit_code": 0}
        class Encoder:
            def __init__(self, path, *_): self.path = path; self.frames = []
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def write(self, color): self.frames.append(color)
            def finish(self):
                self.path.write_bytes(b"encoded")
                return self.path
        count, plane = 3, 64 * 64 * 4
        frames = [(bytes([i + 1]) * plane, bytes([10 + i]) * plane,
                   {"pts_ns": i, "visible": i > 0, "reset": i == 0}) for i in range(count)]
        manifest = {"width": 64, "height": 64, "frames": [{}] * count, "visible_count": 2,
                    "guide_settings": asdict(GuideSettings()), "color_pipeline": {}}
        look = {"schema_version": 2, "nr_enabled": True, "mix": 1.0}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("comfy_dlss_experimental.stream_render.NativeRelay", Session), \
                    patch("comfy_dlss_experimental.stream_render.DirectNRClient", Client), \
                    patch("comfy_dlss_experimental.stream_render.StreamEncoder", Encoder), \
                    patch("comfy_dlss_experimental.stream_render.prepared_frames", return_value=iter(frames)), \
                    patch("comfy_dlss_experimental.video_pipeline.snapshot_runtime", return_value=(root, root)), \
                    patch("comfy_dlss_experimental.video_pipeline.relay_environment", return_value=(None, {})):
                _, report = render_stream(manifest, {"runtime_key": "a" * 64}, build_pass_stack(2, look),
                    root, root / "job", 1, lambda: False, lambda *_: None)
            self.assertEqual(calls, [(0, 1, 10, 0, True), (1, 2, 10, 0, True),
                                     (0, 2, 11, 1, False), (1, 3, 11, 1, False),
                                     (0, 3, 12, 2, False), (1, 4, 12, 2, False)])
            self.assertEqual(report["raw_output_sha256"], hashlib.sha256(bytes([4]) * plane + bytes([5]) * plane).hexdigest())
            self.assertTrue(all(s.closed for s in sessions))
            self.assertEqual(list(root.rglob("*.rgba")), [])


HAS_MEDIA = all(importlib.util.find_spec(n) for n in ("av", "cv2", "numpy")) and bool(shutil.which("ffmpeg"))


@unittest.skipUnless(HAS_MEDIA, "requires existing media dependencies")
class StreamingMediaTests(unittest.TestCase):
    def test_stream_matches_cached_pixels_motion_timing_and_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=128x96:rate=24:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
                "-vf", "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
                "-c:v", "libx264", "-threads", "2",
                "-c:a", "aac", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
                "-color_trc", "bt709", str(source)], check=True, capture_output=True, timeout=30)
            req, guides = ClipRequest(.5, .5, .25), GuideSettings()
            policy = InputColorPolicy(normalize_to_srgb=True)
            cached = prepare_clip(source, root / "cached", req, guides, color_policy=policy)
            stream = inspect_stream(source, req, guides, policy, lambda: False, lambda *_: None)
            self.assertEqual(stream["visible_count"], cached["visible_count"])
            with (root / "cached/color.rgba").open("rb") as colors, (root / "cached/motion.rg16f").open("rb") as motions:
                with StreamEncoder(root / "streamed.mp4", stream, lambda: False) as encoder:
                    for i, (c, m, info) in enumerate(prepared_frames(stream, guides, lambda: False)):
                        self.assertEqual(c, colors.read(len(c)))
                        self.assertEqual(m, motions.read(len(m)))
                        self.assertEqual(info["reset"], cached["frames"][i]["reset"])
                        if info["visible"]:
                            encoder.write(c)
                    encoder.finish()
            out = probe_media(root / "streamed.mp4")
            self.assertTrue(out["has_audio"])
            self.assertEqual(out["video"]["color_transfer"], cached["metadata"]["video"]["color_transfer"])
            self.assertAlmostEqual(float(out["video"]["duration"]), .5, places=3)
            self.assertFalse((root / "streamed.partial.mp4").exists())

    def test_failed_stream_encoder_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {"width": 64, "height": 64, "fps": "24", "visible_count": 24,
                        "visible_start_index": 0, "frames": [{"pts_ns": 0}],
                        "metadata": {"has_audio": False, "video": {"color_transfer": "bt709"}}}
            with self.assertRaisesRegex(RuntimeError, "intentional"):
                with StreamEncoder(root / "out.mp4", manifest, lambda: False) as encoder:
                    encoder.write(bytes(64 * 64 * 4))
                    raise RuntimeError("intentional")
            self.assertFalse((root / "out.mp4").exists())
            self.assertFalse((root / "out.partial.mp4").exists())
            self.assertIsNotNone(encoder.process.poll())
