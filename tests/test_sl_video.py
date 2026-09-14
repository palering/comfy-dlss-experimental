"""Real host-neutral camera/depth/dispatch/storage; fake decode/GPU/encoder."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.execution_context import ExecutionContext
from comfy_dlss_experimental.feature_requirements import requirements_report
from comfy_dlss_experimental.media_pipeline import append_sl, assemble_input, lower_sl_pipeline, pipeline_report
from comfy_dlss_experimental.sl_video import SLVideoInput, render_sl_video, sl_input_from_pipeline
from tests.test_camera_provider import make_camera_inputs
from tests.test_media_pipeline import node_modules, sequence
from tests import test_sl_execution
from tests.test_sl_runtime import make_runtime


@unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy optional execution dependency")
class SLVideoTests(unittest.TestCase):
    fake_backends = test_sl_execution.ReconstructionExecutionTests.fake_backends

    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "tmp" / "sl-video-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "video.mp4"
        self.path.write_bytes(b"explicit fake decoder fixture")
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.sequence = sequence()
        self.sequence["public"]["duration"] = 5/30
        self.sequence["settings"] = {"schema_version": 2, "motion_provider": "zero"}
        self.camera, self.depth, _ = make_camera_inputs(self.root / "inputs", self.sha, video=self.sequence["video"])
        self.pipeline = assemble_input(self.sequence, attachments=[self.depth.to_attachment(), self.camera.to_attachment()])
        self.input_data = sl_input_from_pipeline(self.pipeline)
        self.runtime = make_runtime(self.root / "runtime")
        self.cancel = threading.Event()
        self.progress, self.calls, self.owners, self.encoders, self.closed_inputs = [], [], [], [], []
        self.fail_at = self.cancel_at = None
        self.context = ExecutionContext(self.root / "data", self.root / "temp", self.cancel.is_set,
                                        lambda *args: self.progress.append(args))
        test = self

        class Source:
            def duration(self, **kwargs):
                return 5/30
            def bind(self, request, job, **kwargs):
                return test.path, request
        self.source = Source()

    @contextmanager
    def dispatch(self, *, frame_error=None, change_manifest=None):
        def inspect(source, request, guides, policy, cancelled, progress):
            frames = []
            for record in self.camera.frames:
                time = record.pts_ns / 1e9
                if time + 1e-9 < max(0, request.start-request.pre_roll) or time >= request.start+request.duration-1e-9:
                    continue
                visible = time + 1e-9 >= request.start
                frames.append({"pts_ns": record.pts_ns, "visible": visible})
                if request.single_frame and visible:
                    break
            first = next(i for i, f in enumerate(frames) if f["visible"])
            value = {"source": str(source), "source_sha256": self.sha, "width": 64, "height": 64, "fps": "30",
                     "frames": frames, "visible_start_index": first, "visible_count": len(frames)-first,
                     "metadata": {"has_audio": True, "video": {"width": 64, "height": 64, "color_transfer": "bt709"}},
                     "color_pipeline": {"working_transfer": "iec61966-2-1", "output_transfer": "bt709", "operation": "bt709_to_srgb"}}
            if change_manifest:
                change_manifest(value)
            return value

        def prepared(manifest, *args):
            try:
                if frame_error:
                    raise frame_error
                for item in manifest["frames"]:
                    yield bytes([128, 128, 128, 255]) * 4096, bytes(4096 * 4), {**item, "reset": False}
            finally:
                self.closed_inputs.append(True)

        with ExitStack() as stack:
            stack.enter_context(self.fake_backends())
            stack.enter_context(patch.dict("sys.modules", {"av": ModuleType("av"), "cv2": ModuleType("cv2")}))
            stack.enter_context(patch("comfy_dlss_experimental.sl_video.inspect_stream", inspect))
            stack.enter_context(patch("comfy_dlss_experimental.sl_video.prepared_frames", prepared))
            yield

    def render(self, **kwargs):
        options = dict(source=self.source, context=self.context, input_data=self.input_data, runtime=self.runtime, mode="dlaa")
        options.update(kwargs)
        return render_sl_video(**options)

    def test_file_entry_range_history_reset_and_common_executor(self):
        with self.dispatch():
            output, report = self.render(start_time=2/30, duration=2/30, process_to_end=False, pre_roll=1/30)
        self.assertTrue(output.is_file())
        self.assertEqual((report["frame_count"], report["evaluated_frames"], report["history_frames"]), (2, 3, 1))
        self.assertEqual([pts for pts, _ in self.calls], [33333333, 66666667, 100000000])
        self.assertEqual([kw["metadata"].reset for _, kw in self.calls], [True, True, False])
        self.assertTrue(report["video_input"]["requirements"]["declared_inputs_complete"])
        self.assertFalse(report["video_input"]["requirements"]["gpu_validated"])
        self.assertEqual(self.encoders[0].manifest["color_pipeline"]["operation"], "already_srgb")
        self.assertEqual(self.closed_inputs, [True])
        self.assertTrue(self.owners[0].closed and self.owners[0].ended)

    def test_single_frame_and_default_remaining(self):
        with self.dispatch():
            _, report = self.render(start_time=2/30, single_frame=True, pre_roll=2/30)
        self.assertEqual((report["frame_count"], report["history_frames"]), (1, 2))
        with self.dispatch():
            _, report = self.render(start_time=3/30, pre_roll=0)
        self.assertEqual(report["frame_count"], 2)

    def test_fail_cancel_and_first_decode_failure_close_inputs(self):
        self.fail_at = 1
        with self.dispatch(), self.assertRaisesRegex(RuntimeError, "fake CXR1"):
            self.render()
        self.assertTrue(self.owners[0].closed)
        self.assertFalse(self.encoders[0].published)
        self.assertEqual(self.closed_inputs, [True])
        self.calls.clear()
        self.fail_at, self.cancel_at = None, 1
        with self.dispatch(), self.assertRaises(InterruptedError):
            self.render()
        self.assertTrue(self.owners[-1].closed)
        self.assertEqual(self.closed_inputs, [True, True])
        self.cancel.clear()
        count = len(self.owners)
        with self.dispatch(frame_error=ValueError("decode error")), self.assertRaisesRegex(ValueError, "decode error"):
            self.render()
        self.assertEqual(len(self.owners), count)
        self.assertEqual(self.closed_inputs, [True, True, True])

    def test_wrong_source_fps_grid_and_transfer_rejected_before_gpu(self):
        changes = [lambda v: v.update(source_sha256="b" * 64), lambda v: v.update(fps="24"),
                   lambda v: v.update(width=32), lambda v: v["color_pipeline"].update(working_transfer="bt709"),
                   lambda v: v["frames"][1].update(pts_ns=33340000)]
        for change in changes:
            with self.dispatch(change_manifest=change), self.assertRaises(ValueError):
                self.render()
        self.assertEqual(self.owners, [])

    def test_manifest_plane_source_changes_and_wrong_runtime_rejected(self):
        for runtime in ({**self.runtime, "backend": "owned_sr"}, {**self.runtime, "worker_policy": "persistent"}):
            with self.dispatch(), self.assertRaises(ValueError):
                self.render(runtime=runtime)
        (self.depth.manifest_path.parent / "depth.raw").write_bytes(b"bad")
        with self.dispatch(), self.assertRaisesRegex(ValueError, "SHA256"):
            self.render()
        self.assertEqual(self.owners, [])
        value = json.loads(self.camera.manifest_path.read_text())
        value["provenance"] = "estimated"
        self.camera.manifest_path.write_text(json.dumps(value))
        with self.dispatch(), self.assertRaisesRegex(ValueError, "changed"):
            self.render()

    def test_graph_stage_missing_requirements_and_exact_video_ownership(self):
        bare = append_sl(assemble_input(self.sequence), "dlaa")
        report = pipeline_report(bare)
        self.assertEqual(set(report["missing_inputs"]), {"camera", "depth", "frame_metadata"})
        self.assertFalse(report["ready_for_execution"])
        stage = append_sl(self.pipeline, "dlaa")
        source, params = lower_sl_pipeline(stage)
        self.assertEqual(params["output_width"], 64)
        self.assertEqual(pipeline_report(stage)["missing_inputs"], [])
        self.assertNotIn("video", sl_input_from_pipeline(source).sequence)
        self.assertIsNone(sl_input_from_pipeline(source).camera.source_video)
        with self.assertRaises(ValueError):
            append_sl(stage)
        with self.assertRaisesRegex(ValueError, "source-bound"):
            assemble_input(self.sequence, attachments=[replace(self.camera, source_video=object()).to_attachment()])

    def test_multiplier_resolves_actual_pipeline_grid_not_unused_manual_fields(self):
        result=append_sl(self.pipeline,'performance',999,777,'2x')
        _,params=lower_sl_pipeline(result)
        self.assertEqual((params['output_width'],params['output_height']),(128,128))
        self.assertEqual(pipeline_report(result)['output'],dict(width=128,height=128))
        self.assertEqual(self.pipeline.stages,())

    def test_nodes_reach_sl_executor_without_old_sr_dispatch(self):
        with node_modules() as (core, endpoints):
            nodes = importlib.import_module("comfy_dlss_experimental.nodes.streamline")
            hub = importlib.import_module("comfy_dlss_experimental.nodes.sequence_hub")
            branches = hub.DLSSExperimentalSequenceHub.execute(self.sequence).result
            self.assertTrue(all(branch is self.sequence for branch in branches))
            camera = nodes.DLSSExperimentalCameraInput.execute(branches[1], str(self.camera.manifest_path)).result[0]
            pipeline = core.DLSSExperimentalInputAssembler.execute(branches[0], depth=self.depth, camera=camera).result[0]
            stage = nodes.DLSSExperimentalStreamlineStage.execute(pipeline, "dlaa").result[0]
            with patch("comfy_dlss_experimental.sl_video.run_sl_process", return_value=("VIDEO", {"passed": True})) as execute:
                result = endpoints.DLSSExperimentalPipelineRender.execute(stage, runtime=self.runtime, single_frame=True)
            self.assertEqual(result.result[0], "VIDEO")
            self.assertTrue(execute.call_args.kwargs["single_frame"])
            self.assertEqual(execute.call_args.kwargs["mode"], "dlaa")


class RequirementsTests(unittest.TestCase):
    def test_feature_specific_inputs_and_not_consumed_roles(self):
        nr = requirements_report("owned_cnr1_nr", "nr", {"color", "motion", "timeline", "frame_metadata", "depth", "camera"})
        self.assertEqual(nr["missing_required"], [])
        self.assertEqual(set(nr["unconsumed"]), {"depth", "camera"})
        rr = requirements_report("owned_cxr1_sl", "rr", {"color", "motion", "timeline", "frame_metadata", "depth", "camera"})
        self.assertEqual(set(rr["missing_required"]), {"diffuse_albedo", "specular_albedo", "normal_roughness", "specular_motion", "world_view"})
        with self.assertRaises(ValueError):
            requirements_report("owned_cxr1_sl", "nr")
