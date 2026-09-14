"""Real host/service boundary with fake media/GPU, not GPU acceptance."""
import builtins
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.config import data_root
from comfy_dlss_experimental.execution_context import (ExecutionContext, current_execution_context,
                                                       execution_scope)
from comfy_dlss_experimental.input_policy import InputColorPolicy
from comfy_dlss_experimental.media_clip import ClipRequest
from comfy_dlss_experimental.nr_execution import render_nr_video
from comfy_dlss_experimental.video_source import FileVideoSource


@contextmanager
def no_comfy_imports():
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"comfy", "comfy_api", "folder_paths", "server"}:
            raise AssertionError("Standalone execution imported " + name)
        return original(name, *args, **kwargs)

    with patch("builtins.__import__", guarded):
        yield


class ExecutionBoundaryTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "tmp" / "execution-boundary-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "input.mp4"
        self.path.write_bytes(b"not-a-real-video")
        self.source = FileVideoSource(self.path, trim_start=10, trim_duration=4)
        self.cancel = threading.Event()
        self.progress = []
        self.context = ExecutionContext(self.root / "data", self.root / "temp", self.cancel.is_set,
                                         lambda *args: self.progress.append(args))
        self.metadata = {"video": {"width": 128, "height": 96, "duration": "20"},
                         "input_report": {"format": {"duration": "25"}}}
        self.sequence = {"schema_version": 2, "settings": {"schema_version": 2, "motion_provider": "zero"},
                         "public": {"width": 128, "height": 96},
                         "color_policy": asdict(InputColorPolicy())}
        self.runtime = {"ready": True, "backend": "owned_nr", "runtime_key": "a" * 64}
        self.contract = {"schema_version": 2, "mode": "native", "pre_roll": .5, "warmup_frames": 1}
        self.profile = {"schema_version": 2, "nr_enabled": True, "profile": 1, "mix": 1.0}
        self.requests = []
        self.render_error = None

    @contextmanager
    def media_fixture(self):
        from comfy_dlss_experimental.video_pipeline import _lease_prepared_cache

        def prepare(source, request, guides, root, cancelled, progress, **kwargs):
            self.assertEqual(root, self.context.data_root)
            self.assertEqual(data_root(), root)
            self.requests.append((source, request))
            prepared = root / "prepared-clips" / ("b" * 64)
            prepared.mkdir(parents=True, exist_ok=True)
            _lease_prepared_cache(prepared, retain=kwargs["retain"])
            return prepared, {"source_sha256": "c" * 64, "frames": [{}, {}],
                              "visible_count": 2, "metadata": {}}, False

        def render(prepared, manifest, runtime, profile, root, job, warmup, cancelled, progress):
            if self.render_error:
                raise self.render_error
            self.assertEqual(data_root(), self.context.data_root)
            progress("streaming_nr", 1, 2)
            job.mkdir(parents=True)
            output = job / "output.mp4"
            output.write_bytes(b"fake-encoded-output")
            return output, {"passed": True}

        with ExitStack() as stack:
            stack.enter_context(no_comfy_imports())
            stack.enter_context(patch("comfy_dlss_experimental.video_source.probe_media", return_value=self.metadata))
            stack.enter_context(patch("comfy_dlss_experimental.media_tools.resolve_media_tools", return_value={
                "ready": True, "ffmpeg": {"path": "unused-ffmpeg"}, "ffprobe": {"path": "unused-ffprobe"}}))
            stack.enter_context(patch("comfy_dlss_experimental.nr_execution.prepared_cache", side_effect=prepare))
            stack.enter_context(patch("comfy_dlss_experimental.nr_execution.render_variant", side_effect=render))
            yield

    def render(self, **kwargs):
        return render_nr_video(source=self.source, context=self.context, sequence=self.sequence,
            runtime=self.runtime, contract=self.contract, profile=self.profile, start_time=1,
            duration=2, process_to_end=False, **kwargs)

    def test_file_job_runs_without_comfy_and_preserves_trim_report_and_cache_policy(self):
        with self.media_fixture():
            output, report = self.render(retain_prepared_cache=False)
        self.assertEqual(output.read_bytes(), b"fake-encoded-output")
        self.assertTrue(output.is_relative_to(self.context.temp_root))
        self.assertEqual(self.requests[0][1], ClipRequest(11, 2, .5, 1))
        self.assertTrue(report["prepared_cache_retention"]["removed"])
        self.assertEqual(report["frame_count"], 2)
        self.assertEqual(report["execution"]["state"], "success")
        self.assertEqual(self.progress, [("rendering_nr", 0, 2), ("streaming_nr", 1, 2)])
        self.assertEqual(len(list((self.context.data_root / "executions").glob("*.json"))), 1)
        self.assertIsNone(current_execution_context())

    def test_failed_render_releases_lease_but_retains_prepared_data(self):
        from comfy_dlss_experimental.video_pipeline import _PREPARED_LEASES
        self.render_error = RuntimeError("fake GPU failed")
        with self.media_fixture(), self.assertRaisesRegex(RuntimeError, "fake GPU"):
            self.render(retain_prepared_cache=False)
        prepared = self.context.data_root / "prepared-clips" / ("b" * 64)
        self.assertTrue(prepared.is_dir())
        self.assertNotIn(prepared, _PREPARED_LEASES)
        self.assertIsNone(current_execution_context())

    def test_pre_cancelled_job_does_not_prepare_or_create_directories(self):
        self.cancel.set()
        with no_comfy_imports(), self.assertRaises(InterruptedError):
            self.render()
        self.assertFalse(self.context.data_root.exists())
        self.assertFalse(self.context.temp_root.exists())

    def test_core_rejects_a_comfy_object_in_metadata(self):
        self.sequence["video"] = object()
        with self.media_fixture(), self.assertRaisesRegex(ValueError, "through source"):
            self.render()
        self.assertEqual(self.requests, [])

    def test_file_source_clamps_view_context_and_never_materializes(self):
        with no_comfy_imports(), patch("comfy_dlss_experimental.video_source.probe_media", return_value=self.metadata):
            path, request = self.source.bind(ClipRequest(1, 10, 5), self.root / "unused")
        self.assertEqual(path, self.path)
        self.assertEqual(request, ClipRequest(11, 3, 1))
        self.assertFalse((self.root / "unused").exists())

    def test_file_source_rejects_invalid_temporal_views(self):
        for kwargs in ({"trim_start": True}, {"trim_start": -1}, {"trim_duration": float("nan")},
                       {"trim_duration": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FileVideoSource(self.path, **kwargs)
        with patch("comfy_dlss_experimental.video_source.probe_media", return_value=self.metadata):
            with self.assertRaisesRegex(ValueError, "active view"):
                self.source.bind(ClipRequest(5, 1), self.root)
        self.metadata["video"]["duration"] = "nan"
        with patch("comfy_dlss_experimental.video_source.probe_media", return_value=self.metadata):
            with self.assertRaisesRegex(ValueError, "finite"):
                self.source.duration()

    def test_execution_roots_are_explicit_and_scoped(self):
        other = ExecutionContext(self.root / "other-data", self.root / "other-temp")
        with no_comfy_imports(), execution_scope(self.context):
            self.assertEqual(data_root(), self.context.data_root)
            with execution_scope(other):
                self.assertEqual(data_root(), other.data_root)
            self.assertEqual(data_root(), self.context.data_root)
        self.assertIsNone(current_execution_context())
        with self.assertRaises(ValueError):
            ExecutionContext(Path("relative"), self.root)

    def test_quota_reservations_are_shared_per_temp_root_not_between_independent_roots(self):
        from comfy_dlss_experimental.storage_manager import DEFAULTS, managed_job
        other = ExecutionContext(self.root / "other-data", self.root / "other-temp")

        @managed_job
        def nested_job():
            return True

        @managed_job
        def outer_job():
            with self.assertRaisesRegex(ValueError, "quota"):
                nested_job()
            with execution_scope(other):
                self.assertTrue(nested_job())

        with no_comfy_imports(), execution_scope(self.context), \
             patch("comfy_dlss_experimental.storage_manager.settings", return_value=DEFAULTS | {"temporary_gib": 3}):
            outer_job()

    def test_resident_compatibility_does_not_cross_explicit_data_roots(self):
        from comfy_dlss_experimental.direct_nr import DirectNRSettings
        from comfy_dlss_experimental.resident_worker import compatibility_key
        settings = DirectNRSettings(128, 96, 2)
        first = compatibility_key(self.runtime, settings, {}, owner_root=self.context.data_root)
        second = compatibility_key(self.runtime, settings, {}, owner_root=self.root / "other-data")
        self.assertNotEqual(first, second)

    def test_comfy_node_is_only_source_context_and_result_adapter(self):
        from comfy_dlss_experimental.node_execution import run_process
        from comfy_dlss_experimental.comfy_adapter import ComfyVideoSource
        api = ModuleType("comfy_api.latest")
        api.InputImpl = SimpleNamespace(VideoFromFile=lambda path: ("wrapped-video", path))
        video, report = object(), {"passed": True}
        sequence = {**self.sequence, "video": video}
        with patch.dict("sys.modules", {"comfy_api.latest": api}), \
             patch("comfy_dlss_experimental.comfy_adapter.comfy_execution_context", return_value=self.context), \
             patch("comfy_dlss_experimental.node_execution.render_nr_video", return_value=(self.path, report)) as execute:
            result = run_process(sequence=sequence, runtime=self.runtime, contract=self.contract,
                                 profile=self.profile, start_time=1, duration=2, scale=100)
        self.assertEqual(result, (("wrapped-video", str(self.path)), report))
        self.assertIs(execute.call_args.kwargs["source"].video, video)
        self.assertIsInstance(execute.call_args.kwargs["source"], ComfyVideoSource)
        self.assertNotIn("video", execute.call_args.kwargs["sequence"])
        self.assertIs(sequence["video"], video)
