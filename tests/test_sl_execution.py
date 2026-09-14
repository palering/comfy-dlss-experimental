"""Real execution/storage/context code with fake GPU/encoder, not GPU acceptance."""
from contextlib import ExitStack, contextmanager
import importlib.util
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.execution_context import ExecutionContext, current_execution_context
from comfy_dlss_experimental.sl_bundle import load_bundle
from comfy_dlss_experimental.sl_execution import render_reconstruction, output_rgba8
from tests.test_execution_boundary import no_comfy_imports
from tests.test_sl_bundle import make_bundle
from tests.test_sl_runtime import make_runtime


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy is an optional execution dependency")
class ReconstructionExecutionTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "tmp" / "sl-execution-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        path, _value = make_bundle(self.root / "bundle", rr=True, count=5)
        self.bundle = load_bundle(path)
        self.runtime = make_runtime(self.root / "runtime")
        self.cancel = threading.Event()
        self.progress = []
        self.context = ExecutionContext(self.root / "data", self.root / "temp", self.cancel.is_set,
                                        lambda *args: self.progress.append(args))
        self.calls, self.owners, self.encoders = [], [], []
        self.fail_at = self.cancel_at = None

    @contextmanager
    def fake_backends(self):
        test = self

        class Owner:
            def __init__(self, *args, **kwargs):
                test.owners.append(self)
                self.client = self
                self.run_marker = None
                self.cleanup = {}
                self.closed = self.ended = self.stopped = False
                test.assertEqual(kwargs["feature"], "sl")
                test.assertIsNone(args[2])

            def create(self, settings, session_id):
                self.settings = settings
                test.assertEqual(session_id, 1)

            def process(self, color, motion, depth, pts_ns, **kwargs):
                index = len(test.calls)
                test.calls.append((pts_ns, kwargs))
                test.assertIsNotNone(kwargs["camera"])
                if self.settings.feature == "rr":
                    test.assertIsNotNone(kwargs["rr"])
                if index == test.fail_at:
                    raise RuntimeError("fake CXR1 evaluation failed")
                if index == test.cancel_at:
                    test.cancel.set()
                return color

            def end(self):
                self.ended = True

            def shutdown(self):
                self.stopped = True

            def close(self):
                self.closed = True
                self.cleanup = {"remaining_owned_processes": 0}

            def cancel(self):
                pass

        class Encoder:
            def __init__(self, path, manifest, cancelled):
                self.path, self.manifest = Path(path), manifest
                self.frames = []
                self.closed = self.published = False
                test.encoders.append(self)

            def __enter__(self):
                return self

            def write(self, data):
                self.frames.append(data)

            def finish(self):
                test.assertEqual(len(self.frames), self.manifest["visible_count"])
                self.path.write_bytes(b"fake encoded video")
                self.published = True
                return self.path

            def __exit__(self, *_):
                self.closed = True

        with ExitStack() as stack:
            stack.enter_context(no_comfy_imports())
            stack.enter_context(patch("comfy_dlss_experimental.sl_execution.OwnedWorkerProcess", Owner))
            stack.enter_context(patch("comfy_dlss_experimental.sl_execution.StreamEncoder", Encoder))
            stack.enter_context(patch("comfy_dlss_experimental.sl_execution.relay_environment", return_value=(None, {})))
            stack.enter_context(patch("comfy_dlss_experimental.media_tools.resolve_media_tools", return_value={
                "ready": True, "ffmpeg": {"path": "unused"}, "ffprobe": {"path": "unused"}}))
            yield

    def render(self, **kwargs):
        return render_reconstruction(context=self.context, bundle=self.bundle, runtime=self.runtime,
                                     feature="rr", mode="dlaa", **kwargs)

    def test_range_history_cuts_output_and_host_neutrality(self):
        with self.fake_backends():
            output, report = self.render(start_frame=2, frame_count=2, history_frames=1)
        self.assertTrue(output.is_file())
        self.assertEqual([pts for pts, _ in self.calls], [33333333, 66666667, 100000000])
        self.assertEqual([k["metadata"].reset for _, k in self.calls], [True, True, False])
        self.assertEqual((report["frame_count"], report["evaluated_frames"], report["history_frames"]), (2, 3, 1))
        self.assertEqual(report["feature"], "rr_dlaa")
        self.assertEqual(report["execution"]["state"], "success")
        self.assertEqual(report["color_pipeline"]["output_transfer"], "srgb")
        self.assertTrue(self.owners[0].closed and self.owners[0].ended and self.owners[0].stopped)
        self.assertEqual(len(self.encoders[0].frames), 2)
        self.assertIsNone(current_execution_context())

    def test_default_remaining_and_one_frame_preview(self):
        with self.fake_backends():
            _output, report = self.render(start_frame=4, history_frames=0)
        self.assertEqual(report["frame_count"], 1)
        self.assertEqual(report["evaluated_frames"], 1)
        self.assertEqual(report["first_source_pts_ns"], 133333333)

    def test_failure_closes_owned_process_without_publishing(self):
        self.fail_at = 1
        with self.fake_backends(), self.assertRaisesRegex(RuntimeError, "fake CXR1"):
            self.render()
        self.assertTrue(self.owners[0].closed)
        self.assertTrue(self.encoders[0].closed)
        self.assertFalse(self.encoders[0].published)
        self.assertIsNone(current_execution_context())

    def test_cancellation_closes_without_success_or_final_output(self):
        self.cancel_at = 1
        with self.fake_backends(), self.assertRaises(InterruptedError):
            self.render()
        self.assertTrue(self.owners[0].closed)
        self.assertFalse(self.encoders[0].published)

    def test_bad_range_and_stale_first_plane_fail_before_gpu(self):
        for options in ({"start_frame": 5}, {"start_frame": 4, "frame_count": 2}, {"history_frames": True}):
            with self.fake_backends(), self.assertRaises(ValueError):
                self.render(**options)
        self.assertEqual(self.owners, [])
        (self.bundle.manifest_path.parent / "color.bin").write_bytes(b"x" * 32768)
        with self.fake_backends(), self.assertRaisesRegex(ValueError, "SHA-256"):
            self.render()
        self.assertEqual(self.owners, [])

    def test_linear_export_is_not_direct_dark_quantization_and_alpha_not_gamma_corrected(self):
        data = struct.pack("<4e", .25, .5, 1, .5)
        srgb, clipped = output_rgba8(data, 1, 1, "linear_sdr")
        self.assertEqual(list(srgb), [137, 188, 255, 128])
        self.assertEqual(clipped, 0)
        plain, _ = output_rgba8(data, 1, 1, "srgb")
        self.assertEqual(list(plain), [64, 128, 255, 128])
        for raw in (b"", struct.pack("<4e", float("nan"), 0, 0, 1)):
            with self.assertRaises(ValueError):
                output_rgba8(raw, 1, 1, "srgb")

    def test_pre_cancelled_creates_no_job(self):
        self.cancel.set()
        with self.fake_backends(), self.assertRaises(InterruptedError):
            self.render()
        self.assertEqual(self.owners, [])
        self.assertFalse(self.context.temp_root.exists())

    def test_audio_is_hash_bound_and_range_checked_before_gpu(self):
        import json
        import hashlib
        path = self.bundle.manifest_path
        value = json.loads(path.read_text())
        audio = path.parent / "audio.wav"
        audio.write_bytes(b"fake audio")
        value["audio_source"] = {"path": audio.name, "sha256": hashlib.sha256(audio.read_bytes()).hexdigest()}
        path.write_text(json.dumps(value))
        self.bundle = load_bundle(path)
        result = SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "audio", "duration": "1", "start_time": "0"}]}))
        with self.fake_backends(), patch("comfy_dlss_experimental.sl_execution.run_cancellable", return_value=result):
            _output, report = self.render(start_frame=2, frame_count=1, history_frames=0)
        self.assertTrue(report["has_audio"])
        self.assertEqual(self.encoders[0].manifest["source"], str(audio))
        self.assertEqual(self.encoders[0].manifest["frames"][0]["pts_ns"], 66666667)
        audio.write_bytes(b"changed")
        with self.fake_backends(), self.assertRaisesRegex(ValueError, "audio source SHA-256"):
            self.render()
