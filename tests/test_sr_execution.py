"""CPU media-boundary smoke tests; fake CSR1 GPU/encoder, real guide files.

These tests prove dispatch, plane/timestamp contracts and cleanup, not SR image
quality, NGX optimal-size support or a successful native GPU evaluation.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.external_guides import load_external_guide, load_guide_manifest
from comfy_dlss_experimental.media_pipeline import append_nr, assemble_input
from comfy_dlss_experimental.sr_contract import SRSettings
from comfy_dlss_experimental.sr_execution import render_sr_stream, validate_sr_execution
from comfy_dlss_experimental.sr_execution import (SRVideoInput, render_sr_input_stream, render_sr_video,
                                                 sr_input_from_pipeline)
from comfy_dlss_experimental.execution_context import ExecutionContext
from comfy_dlss_experimental.video_source import FileVideoSource


class SRExecutionTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "tmp" / "sr-execution-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.video = object()
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"cpu-fixture-not-a-decoded-video")
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.pts = [0, 33_333_333, 66_666_667]
        self.sequence = {"schema_version": 2, "video": self.video,
            "settings": {"schema_version": 2, "motion_provider": "zero"},
            "public": {"width": 64, "height": 64, "duration": .1},
            "input_report": {"ready": True, "issues": []}}
        self.runtime = {"backend": "owned_sr", "ready": True, "worker_policy": "isolated",
            "runtime_key": "a" * 64, "compatibility": {"project_id": "12345678-1234-1234-1234-123456789abc"}}
        self.depth_manifest = {"schema_version": 1,
            "source": {"sha256": self.sha, "view_id": "source", "width": 64, "height": 64,
                       "time_origin": "video_stream_start"},
            "role": "depth", "semantics": "device_z", "units": "zero_to_one",
            "grid": {"width": 64, "height": 64, "sampling": "pixel_centers"},
            "dtype": "float32_le", "storage": "raw",
            "metadata": {"reversed_z": False, "near": .1, "far": 100, "projection": "perspective"},
            "frames": []}
        for index, pts in enumerate(self.pts):
            data = struct.pack("<f", .25 + index * .125) * (64 * 64)
            name = f"depth-{index}.raw"
            (self.root / name).write_bytes(data)
            self.depth_manifest["frames"].append({"path": name, "sha256": hashlib.sha256(data).hexdigest(),
                                                   "pts_ns": pts, "reset": index == 2})
        self.depth_path = self.root / "depth.json"
        self.reload_depth()
        self.manifest = {"streaming": True, "source": str(self.source), "source_sha256": self.sha,
            "width": 64, "height": 64, "fps": "30", "visible_count": 2, "visible_start_index": 1,
            "frames": [{"pts_ns": pts, "visible": index > 0} for index, pts in enumerate(self.pts)],
            "metadata": {"has_audio": True, "video": {"width": 64, "height": 64, "color_transfer": "bt709"}},
            "color_pipeline": {"working_transfer": "iec61966-2-1", "output_transfer": "bt709"}}
        self.frames = [(bytes([index + 1]) * (64 * 64 * 4), bytes(64 * 64 * 4),
                       {"pts_ns": pts, "visible": index > 0, "reset": index == 0})
                      for index, pts in enumerate(self.pts)]
        self.sessions, self.encoders, self.calls, self.closed_inputs = [], [], [], []
        self.process_error = None
        self.cancelled = threading.Event()

    def reload_depth(self):
        self.depth_path.write_text(json.dumps(self.depth_manifest))
        self.depth = load_external_guide(self.depth_path, source_video=self.video)
        self.pipeline = assemble_input(self.sequence, attachments=[self.depth.to_attachment()])

    @contextmanager
    def dispatch(self, *, frame_error=None):
        owner = self

        class NativeSettings:
            def __init__(self, width, height, output_width, output_height, settings):
                self.width, self.height, self.output_width, self.output_height = width, height, output_width, output_height
                self.settings = settings
            def encode(self):
                return b"validated-CSR1-settings"

        class Client:
            def create(self, settings, *, session_id):
                self.settings = settings
                owner.calls.append(("create", session_id, settings.width, settings.height,
                                    settings.output_width, settings.output_height))
            def process(self, color, motion, depth, pts_ns, *, metadata):
                owner.calls.append(("process", len(color), len(motion), len(depth), pts_ns, metadata.reset))
                if owner.process_error is not None:
                    if isinstance(owner.process_error, InterruptedError):
                        owner.cancelled.set()
                        raise RuntimeError("socket closed by cancellation")
                    raise owner.process_error
                return struct.pack("<e", .5) * (self.settings.output_width * self.settings.output_height * 4)
            def end(self):
                owner.calls.append(("end",))

        class Session:
            def __init__(self, worker, model, caller, job, **kwargs):
                self.args = (worker, model, caller, job, kwargs)
                self.client, self.cleanup, self.run_marker = Client(), {"remaining": 0}, "only-this-worker"
                self.closed = self.shutdown_called = False
                owner.sessions.append(self)
            def shutdown(self):
                self.shutdown_called = True
            def close(self):
                self.closed = True
            def cancel(self):
                owner.cancelled.set()

        class Encoder:
            def __init__(self, path, manifest, cancelled):
                self.path, self.manifest = path, manifest
                self.frames, self.closed = [], False
                owner.encoders.append(self)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.closed = True
            def write(self, color):
                if len(color) != self.manifest["width"] * self.manifest["height"] * 4:
                    raise ValueError("wrong output extent")
                self.frames.append(color)
            def finish(self):
                self.path.write_bytes(b"fake-encoded-output")
                return self.path

        def frames(*args):
            try:
                if frame_error:
                    raise frame_error
                yield from owner.frames
            finally:
                owner.closed_inputs.append(True)

        def from_half(data, expected_bytes):
            if len(data) != expected_bytes * 2:
                raise ValueError("wrong FP16 output extent")
            return bytes([128]) * expected_bytes

        native = ModuleType("comfy_dlss_experimental.sr_worker")
        native.OwnedSRSettings = NativeSettings
        runtime = ModuleType("comfy_dlss_experimental.sr_runtime")
        runtime.snapshot_sr = lambda *_: (self.root / "worker.exe", self.root / "nvngx_dlss.dll")
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {native.__name__: native, runtime.__name__: runtime}))
            for name, value in {"OwnedWorkerProcess": Session, "StreamEncoder": Encoder,
                    "prepared_frames": frames, "rgba8_to_half": lambda data: bytes(len(data) * 2),
                    "half_to_rgba8": from_half, "relay_environment": lambda *_: (None, {})}.items():
                stack.enter_context(patch("comfy_dlss_experimental.sr_execution." + name, value))
            stack.enter_context(patch("comfy_dlss_experimental.resident_worker.resident_worker.retire_idle"))
            yield

    def render(self, **kwargs):
        return render_sr_stream(self.manifest, kwargs.get("pipeline", self.pipeline),
            kwargs.get("runtime", self.runtime), kwargs.get("settings", SRSettings()),
            kwargs.get("output_width", 128), kwargs.get("output_height", 128),
            self.root / "runtime", self.root / "job", self.cancelled.is_set)

    def test_csr1_dispatch_evaluates_preroll_at_input_extent_and_encodes_output_extent(self):
        before = deepcopy(self.manifest)
        with self.dispatch():
            output, report = self.render()
        self.assertTrue(output.is_file())
        self.assertEqual(self.calls[0], ("create", 1, 64, 64, 128, 128))
        evaluations = [call for call in self.calls if call[0] == "process"]
        self.assertEqual(len(evaluations), 3)
        self.assertEqual([call[4] for call in evaluations], self.pts)
        self.assertEqual([call[5] for call in evaluations], [True, False, True])
        self.assertTrue(all(call[1:4] == (64 * 64 * 8, 64 * 64 * 4, 64 * 64 * 4) for call in evaluations))
        self.assertEqual(len(self.encoders[0].frames), 2)
        self.assertEqual((report["frame_count"], report["evaluated_frames"], report["pre_roll_frames"]), (2, 3, 1))
        self.assertEqual(report["protocol"], "CSR1")
        self.assertEqual(report["output_dimensions"], {"width": 128, "height": 128})
        self.assertEqual(self.sessions[0].args[4]["feature"], "sr")
        self.assertIsNone(self.sessions[0].args[2])  # No NR caller shim.
        self.assertTrue(self.sessions[0].closed and self.sessions[0].shutdown_called)
        self.assertEqual(self.closed_inputs, [True])
        self.assertEqual(self.encoders[0].manifest["metadata"], self.manifest["metadata"])
        self.assertEqual(self.manifest, before)
        self.assertTrue(report["source_metadata"]["has_audio"])
        self.assertEqual(report["raw_disk_bytes"], 0)
        self.assertEqual(list((self.root / "job").rglob("*.raw")), [])

    def test_dlaa_still_dispatches_csr1_with_equal_dimensions(self):
        with self.dispatch():
            _, report = self.render(settings=SRSettings(mode="dlaa"), output_width=64, output_height=64)
        self.assertEqual(report["feature"], "dlaa")
        self.assertEqual(self.calls[0][2:], (64, 64, 64, 64))
        self.assertEqual(len([call for call in self.calls if call[0] == "process"]), 3)

    def test_neutral_sr_input_drops_graph_object_but_keeps_content_contract(self):
        data = sr_input_from_pipeline(self.pipeline)
        self.assertNotIn("video", data.sequence)
        self.assertIsNone(data.depth.source_video)
        self.assertEqual(data.depth.cache_identity, self.depth.cache_identity)
        self.assertIs(self.depth.source_video, self.video)
        with self.dispatch():
            _, report = render_sr_input_stream(self.manifest, data, self.runtime, SRSettings(), 128, 128,
                self.root / "runtime", self.root / "job", self.cancelled.is_set)
        self.assertEqual(report["frame_count"], 2)
        self.assertEqual(len(self.sessions), 1)

    def test_standalone_sr_file_entry_never_imports_comfy(self):
        import builtins
        original = builtins.__import__

        def guard(name, *args, **kwargs):
            if name.split(".")[0] in {"comfy", "comfy_api", "folder_paths", "server"}:
                raise AssertionError("Standalone SR imported " + name)
            return original(name, *args, **kwargs)

        data = SRVideoInput({key: value for key, value in self.sequence.items() if key != "video"},
                            load_guide_manifest(self.depth_path))
        self.manifest["storage_plan"] = {"mode": "streaming"}
        progress = []
        context = ExecutionContext(self.root / "runtime", self.root / "temporary", self.cancelled.is_set,
                                     lambda *args: progress.append(args))
        with self.dispatch(), patch("builtins.__import__", guard), \
             patch("comfy_dlss_experimental.sr_execution._require_media_dependencies"), \
             patch("comfy_dlss_experimental.media_tools.resolve_media_tools", return_value={"ready": True}), \
             patch("comfy_dlss_experimental.video_source.probe_media", return_value={"video": {"duration": ".1"}}), \
             patch("comfy_dlss_experimental.sr_execution.inspect_stream", return_value=self.manifest):
            output, report = render_sr_video(source=FileVideoSource(self.source), context=context, input_data=data,
                runtime=self.runtime, settings=SRSettings(), output_width=128, output_height=128)
        self.assertTrue(output.is_relative_to(context.temp_root))
        self.assertEqual(report["execution"]["state"], "success")
        self.assertEqual(progress[0], ("rendering_sr", 0, 3))
        self.assertEqual(progress[-1], ("streaming_sr", 3, 3))
        self.assertTrue(self.sessions[0].closed)

    def test_neutral_sr_input_still_rejects_wrong_decoded_content(self):
        data = sr_input_from_pipeline(self.pipeline)
        self.manifest["source_sha256"] = "f" * 64
        with self.dispatch(), self.assertRaisesRegex(ValueError, "content hash"):
            render_sr_input_stream(self.manifest, data, self.runtime, SRSettings(), 128, 128,
                                    self.root / "runtime", self.root / "job")
        self.assertEqual(self.sessions, [])
        with self.assertRaisesRegex(ValueError, "VIDEO"):
            SRVideoInput(self.sequence, self.depth)

    def test_error_and_cancel_reap_only_owned_worker_and_close_decoder_encoder(self):
        for error in (RuntimeError("CSR1 evaluate failed"), InterruptedError("cancelled")):
            with self.subTest(error=type(error).__name__):
                self.process_error = error
                with self.dispatch(), self.assertRaises(type(error)):
                    self.render()
                self.assertTrue(self.sessions[-1].closed)
                self.assertFalse(self.sessions[-1].shutdown_called)
                self.assertTrue(self.encoders[-1].closed)
                self.assertFalse((self.root / "job" / "output.mp4").exists())
                self.assertFalse((self.root / "job" / "report.json").exists())
                self.assertTrue(self.closed_inputs)
                (self.root / "job").rmdir()
                self.cancelled.clear()

    def test_guide_dependency_failure_precedes_sr_process_creation(self):
        with self.dispatch(frame_error=FileNotFoundError("NVIDIA optical-flow helper is not installed")), \
                self.assertRaisesRegex(FileNotFoundError, "helper"):
            self.render()
        self.assertEqual(self.sessions, [])
        self.assertEqual(self.closed_inputs, [True])

    def test_missing_depth_or_non_device_depth_rejected_without_gpu(self):
        missing = assemble_input(self.sequence)
        wrong = replace(self.depth, semantics="inverse_view_z", units="relative")
        for pipeline in (missing, assemble_input(self.sequence, attachments=[wrong.to_attachment()])):
            with self.subTest(pipeline=pipeline), self.dispatch(), self.assertRaisesRegex(ValueError, "depth"):
                self.render(pipeline=pipeline)
        self.assertEqual(self.sessions, [])

    def test_nr_stages_and_unsupported_metadata_do_not_silently_fall_back(self):
        nr = append_nr(self.pipeline, {"schema_version": 2, "nr_enabled": True, "mix": 1})
        cases = ({"pipeline": nr}, {"runtime": dict(self.runtime, backend="owned_nr")},
                 {"runtime": dict(self.runtime, worker_policy="persistent")},
                 {"settings": SRSettings(hdr=True)}, {"settings": SRSettings(auto_exposure=False)},
                 {"settings": SRSettings(jitter_policy="external_render_metadata")},
                 {"settings": SRSettings(depth_inverted=True)},
                 {"output_width": 5000, "output_height": 5000}, {"output_width": 129, "output_height": 129})
        for values in cases:
            with self.subTest(values=values), self.dispatch(), self.assertRaises(ValueError):
                self.render(**values)
        self.assertEqual(self.sessions, [])

    def test_depth_timestamp_missing_preroll_and_changed_manifest_fail_before_gpu(self):
        self.depth_manifest["frames"] = self.depth_manifest["frames"][1:]
        self.reload_depth()
        with self.dispatch(), self.assertRaisesRegex(ValueError, "pre-roll"):
            self.render()
        self.depth_manifest["metadata"]["far"] = 101
        self.depth_path.write_text(json.dumps(self.depth_manifest))
        with self.dispatch(), self.assertRaisesRegex(ValueError, "manifest changed"):
            self.render()
        self.assertEqual(self.sessions, [])

    def test_missing_or_corrupt_first_depth_never_launches_sr(self):
        first = self.root / self.depth_manifest["frames"][0]["path"]
        first.unlink()
        with self.dispatch(), self.assertRaisesRegex(ValueError, "missing"):
            self.render()
        first.write_bytes(bytes(64 * 64 * 4))
        with self.dispatch(), self.assertRaisesRegex(ValueError, "SHA256"):
            self.render()
        self.assertEqual(self.sessions, [])

    def test_content_changed_or_unconverted_color_is_not_accepted_as_srgb(self):
        self.manifest["color_pipeline"]["working_transfer"] = "bt709"
        with self.dispatch(), self.assertRaisesRegex(ValueError, "normalize_to_srgb"):
            self.render()
        self.manifest["color_pipeline"]["working_transfer"] = "iec61966-2-1"
        self.source.write_bytes(b"different source")
        with self.dispatch(), self.assertRaisesRegex(ValueError, "content hash"):
            self.render()
        self.assertEqual(self.sessions, [])

    def test_truncated_input_fails_without_publishing_success(self):
        self.frames = self.frames[:-1]
        with self.dispatch(), self.assertRaisesRegex(ValueError, "every input"):
            self.render()
        self.assertTrue(self.sessions[0].closed)
        self.assertFalse((self.root / "job" / "output.mp4").exists())
        self.assertFalse((self.root / "job" / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
