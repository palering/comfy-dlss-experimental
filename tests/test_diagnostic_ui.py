import ast
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_media_pipeline import node_modules
from comfy_dlss_experimental.diagnostic_ui import diagnostic_ui


class DiagnosticUITests(unittest.TestCase):
    def test_report_is_lossless_text_not_a_comfy_media_descriptor(self):
        report = {"format": {"format_name": "mov,mp4", "duration": "0.267"},
                  "filename": "输入.mp4", "issues": [], "ready": True}
        ui = diagnostic_ui("dlss_input_report", report)
        self.assertIsInstance(ui["dlss_input_report"][0], str)
        self.assertEqual(json.loads(ui["dlss_input_report"][0]), report)
        self.assertIsInstance(report["format"], dict)

    def test_input_adapter_keeps_internal_report_and_string_socket(self):
        with node_modules():
            module = importlib.import_module("comfy_dlss_experimental.nodes.temporal")
            report = {"ready": True, "state": "ready", "format": {"format_name": "mp4"},
                      "active_view": {"width": 640, "height": 360}, "issues": []}
            tools = {"ready": True, "ffprobe": {"available": True, "path": "/ffprobe"}}
            video = object()
            with patch.object(module, "inspect_video_input", return_value=report), \
                    patch("comfy_dlss_experimental.media_tools.resolve_media_tools", return_value=tools):
                output = module.DLSSExperimentalPrepareTemporalSequence.execute(video)
            self.assertIs(output.result[0]["video"], video)
            self.assertIs(output.result[0]["input_report"], report)
            self.assertEqual(json.loads(output.result[1]), report)
            self.assertEqual(json.loads(output.ui["dlss_input_report"][0]), report)

    def test_all_node_ui_diagnostics_use_the_shared_media_safe_boundary(self):
        # Catch a new raw dict-list diagnostic before it enters Comfy Jobs.
        count = 0
        for path in (Path(__file__).resolve().parents[1] / "comfy_dlss_experimental/nodes").glob("*.py"):
            for item in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(item, ast.Call):
                    continue
                for kw in item.keywords:
                    if kw.arg == "ui":
                        count += 1
                        self.assertIsInstance(kw.value, ast.Call, path.name)
                        self.assertEqual(getattr(kw.value.func, "id", None), "diagnostic_ui", path.name)
        self.assertEqual(count, 9)
