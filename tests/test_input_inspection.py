from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.input_inspection import inspect_video_input
from comfy_dlss_experimental.input_policy import InputColorPolicy


class InspectionTests(unittest.TestCase):
    def test_cropped_untagged_video_is_not_silently_materialized(self):
        class Video:
            def get_dimensions(self): return 64, 64
            def get_duration(self): return 2
            def get_stream_source(self): return source
            def get_active_trim_window(self): return 1, 2
        api = types.ModuleType("comfy_api.latest")
        api.InputImpl = types.SimpleNamespace(VideoFromFile=Video)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            source.write_bytes(b"fixture")
            metadata = {"streams":[{"codec_type":"video", "width":128,"height":96,"avg_frame_rate":"24/1"}]}
            with patch.dict("sys.modules", {"comfy_api.latest": api}), patch("comfy_dlss_experimental.input_inspection.inspect_media", return_value=metadata):
                report = inspect_video_input(Video(), InputColorPolicy("fill_missing"), "dis")
            self.assertFalse(report["ready"])
            self.assertTrue(report["active_view"]["cropped"])
            self.assertEqual(report["active_view"]["trim_start"], 1)
            self.assertEqual(report["source_video"]["width"], 128)
            self.assertEqual(report["active_view"]["width"], 64)

    def test_generic_video_defers_headers_without_materialization(self):
        class GenericVideo:
            def get_dimensions(self): return 128, 96
            def get_duration(self): return 120
            def get_components(self): raise AssertionError("No full materialization in inspector")
            def save_to(self, *args, **kwargs): raise AssertionError("No transcode in inspector")
        api = types.ModuleType("comfy_api.latest")
        api.InputImpl = types.SimpleNamespace(VideoFromFile=type("FileVideo", (), {}))
        with patch.dict("sys.modules", {"comfy_api.latest": api}):
            report = inspect_video_input(GenericVideo(), InputColorPolicy(), "zero")
        self.assertEqual(report["state"], "deferred")
        self.assertFalse(report["header_readable"])
        self.assertEqual(report["decode_validation"], "not_run")

    def test_cancelled_probe_is_not_swallowed_as_an_unreadable_video(self):
        class Video:
            def get_dimensions(self): raise InterruptedError("cancelled")
        api = types.ModuleType("comfy_api.latest")
        api.InputImpl = types.SimpleNamespace(VideoFromFile=Video)
        with patch.dict("sys.modules", {"comfy_api.latest": api}), self.assertRaises(InterruptedError):
            inspect_video_input(Video(), InputColorPolicy(), "dis")
