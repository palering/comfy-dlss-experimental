"""Metadata-only edits must not change the serialized workflow contract."""
import ast
from pathlib import Path
import unittest


NODES = Path(__file__).resolve().parents[1] / "comfy_dlss_experimental" / "nodes"


def schema_inputs(filename, class_name):
    tree = ast.parse((NODES / filename).read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "define_schema")
    schema = next(node.value for node in method.body if isinstance(node, ast.Return))
    inputs = next(item.value for item in schema.keywords if item.arg == "inputs")
    return [(ast.literal_eval(call.args[0]), {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords})
            for call in inputs.elts]


class PreviewSchemaMetadataTests(unittest.TestCase):
    def test_preview_preserves_widget_order_and_machine_values(self):
        values = schema_inputs("preview_session.py", "DLSSExperimentalPreviewSession")
        self.assertEqual([name for name, _ in values[:-1]], ["sequence", "runtime", "profile_b", "profile_a",
            "start_time", "duration", "preview_scale", "contract", "preview_mode", "cursor_time"])
        data = dict(values)
        self.assertEqual(values[-1][0], "process_to_end")
        self.assertIs(data["process_to_end"]["default"], False)
        self.assertIs(data["process_to_end"]["optional"], True)
        self.assertEqual(data["duration"]["max"], 86400)
        for name, value in {"start_time": 0.0, "duration": 3.0, "preview_scale": 50,
                            "preview_mode": "range", "cursor_time": 0.0}.items():
            self.assertEqual(data[name]["default"], value)
            self.assertTrue(data[name]["display_name"])
        self.assertEqual(data["preview_mode"]["options"], ["range", "frame"])
        self.assertEqual(data["preview_scale"]["options"], [50, 75, 100])
        self.assertTrue(all(options.get("tooltip") for _, options in values))

    def test_process_preserves_independent_output_range(self):
        values = schema_inputs("process_video.py", "DLSSExperimentalProcessVideo")
        self.assertEqual([name for name, _ in values[:-1]], ["sequence", "runtime", "profile", "start_time", "duration", "scale", "contract"])
        self.assertEqual(values[-1][0], "process_to_end")
        data = dict(values)
        self.assertEqual(data["duration"]["default"], 0.0)
        self.assertEqual(data["scale"]["default"], 100)
        self.assertEqual(data["scale"]["options"], [50, 75, 100])
        for name in ("start_time", "duration", "scale"):
            self.assertTrue(data[name]["display_name"])
            self.assertTrue(data[name]["tooltip"])
