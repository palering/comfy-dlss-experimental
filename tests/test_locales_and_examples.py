import ast
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def node_schemas():
    schemas = {}
    for path in (ROOT / "comfy_dlss_experimental/nodes").glob("*.py"):
        for cls in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(cls, ast.ClassDef):
                continue
            method = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "define_schema"), None)
            if method:
                schema = next(n.value for n in method.body if isinstance(n, ast.Return))
                schemas[cls.name] = {kw.arg: kw.value for kw in schema.keywords}
    return schemas


class LocaleAndExampleTests(unittest.TestCase):
    def test_locales_cover_all_nodes_inputs_outputs_and_static_options(self):
        schemas = node_schemas()
        catalogs = [json.loads((ROOT / "locales" / lang / "nodeDefs.json").read_text(encoding="utf-8")) for lang in ("en", "zh", "zh-TW")]
        for catalog in catalogs:
            self.assertEqual(set(catalog), set(schemas))
            for name, schema in schemas.items():
                entry = catalog[name]
                self.assertTrue(entry["display_name"])
                self.assertTrue(entry["description"])
                inputs = schema["inputs"].elts
                self.assertEqual(set(entry["inputs"]), {ast.literal_eval(i.args[0]) for i in inputs})
                for call in inputs:
                    field = entry["inputs"][ast.literal_eval(call.args[0])]
                    self.assertTrue(field["name"])
                    self.assertTrue(field["tooltip"])
                    options = next((kw.value for kw in call.keywords if kw.arg == "options"), None)
                    if isinstance(options, (ast.List, ast.Tuple)):
                        self.assertEqual(set(field["options"]), {str(v).replace(".", "_") for v in ast.literal_eval(options)})
                self.assertEqual(set(entry["outputs"]), {str(i) for i in range(len(schema["outputs"].elts))})
        def leaves(obj):
            for value in obj.values():
                if isinstance(value, dict): yield from leaves(value)
                else: yield value
        self.assertTrue(all(not re.search("[\u3400-\u9fff]", v) for v in leaves(catalogs[0])))

    def test_legacy_dynamic_choices_have_display_translations_without_new_values(self):
        from comfy_dlss_experimental.nr_options import LOOK_CHOICES
        for lang in ("en", "zh", "zh-TW"):
            catalog = json.loads((ROOT / "locales" / lang / "nodeDefs.json").read_text(encoding="utf-8"))
            for name, choices in LOOK_CHOICES.items():
                self.assertEqual(set(catalog["DLSSExperimentalNRProfile"]["inputs"][name]["options"]), set(choices))
            for cls in ("DLSSExperimentalDISFlow", "DLSSExperimentalNVIDIAFlow"):
                self.assertEqual(set(catalog[cls]["inputs"]["preset"]["options"]), {"均衡 · Balanced", "速度优先 · Fast", "质量优先 · Quality"})

    def test_examples_have_consistent_links_and_only_installed_node_types(self):
        allowed = set(node_schemas()) | {"LoadVideo", "SaveVideo"}
        files = list((ROOT / "example_workflows").glob("*.json"))
        self.assertEqual(len(files), 9)
        workflow_ids = set()
        for file in files:
            data = json.loads(file.read_text(encoding="utf-8"))
            workflow_ids.add(data["id"])
            nodes = {node["id"]: node for node in data["nodes"]}
            self.assertEqual(len(nodes), len(data["nodes"]))
            self.assertTrue({n["type"] for n in nodes.values()} <= allowed)
            links = {link[0]: link for link in data["links"]}
            self.assertEqual(len(links), len(data["links"]))
            for link_id, source, source_slot, target, target_slot, dtype in links.values():
                out, inp = nodes[source]["outputs"][source_slot], nodes[target]["inputs"][target_slot]
                self.assertIn(link_id, out["links"])
                self.assertEqual(inp["link"], link_id)
                self.assertEqual(out["type"], dtype)
                self.assertEqual(inp["type"], dtype)
            for node in nodes.values():
                for inp in node["inputs"]:
                    if inp.get("link") is not None: self.assertIn(inp["link"], links)
                for out in node["outputs"]:
                    for link_id in out.get("links") or []: self.assertIn(link_id, links)
                if node["type"] == "LoadVideo": self.assertEqual(node["widgets_values"], ["example-input.mp4"])
                if node["type"] == "DLSSExperimentalRuntimeConfig":
                    expected_preset = {"dlss_sr_experimental.json": "runtime-presets/owned-sr.json",
                                       "dlss_reconstruction.json": "runtime-presets/owned-sl.json",
                                       "dlss_streamline_video.json": "runtime-presets/owned-sl.json"}.get(file.name, "runtime-presets/default.json")
                    self.assertEqual(node["widgets_values"][4], expected_preset)
                    self.assertEqual(node["widgets_values"][7], "auto")
                if node["type"] == "DLSSExperimentalProcessVideo": self.assertEqual(node["widgets_values"], [0, 0, 100, True])
            self.assertEqual(data["last_node_id"], max(nodes))
            self.assertEqual(data["last_link_id"], max(links))
        self.assertEqual(len(workflow_ids), len(files))
