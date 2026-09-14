from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.flow_provider import FlowProvider
from comfy_dlss_experimental.media_pipeline import (
    GuideAttachment, MediaPipeline, append_nr, assemble_input, lower_nr_pipeline, pipeline_report, select_flow,
)
from comfy_dlss_experimental.nr_effect import build_pass_stack, expand_effect
from comfy_dlss_experimental.video_pipeline import guide_settings


class OpaqueVideo:
    def __deepcopy__(self, _memo):
        raise AssertionError("VIDEO must not be copied")

    def get_duration(self):
        raise AssertionError("Assembly must not decode or inspect VIDEO")


def sequence():
    return {"schema_version": 2, "video": OpaqueVideo(),
            "settings": {"schema_version": 2, "motion_provider": "dis"},
            "public": {"width": 64, "height": 64, "duration": 3},
            "input_report": {"ready": True, "state": "ready", "issues": []}}


def look(**kwargs):
    return {"schema_version": 2, "nr_enabled": True, "mix": 1.0, **kwargs}


def provider(kind="dis", **kwargs):
    return {"schema_version": 1, **asdict(FlowProvider(kind=kind, **kwargs))}


@contextmanager
def node_modules():
    """Minimal schema harness; not a claim of real Comfy execution validation."""
    class InputType:
        def __init__(self, name): self.name = name
        def Input(self, name, **kwargs): return SimpleNamespace(id=name, **kwargs)
        def Output(self, name, **kwargs): return SimpleNamespace(id=name, **kwargs)
    class NodeOutput:
        def __init__(self, *args, **kwargs): self.result = args; self.ui = kwargs.get("ui")
    class Node:
        hidden = SimpleNamespace(unique_id="7", extra_pnginfo={"workflow": {"id": "example"}})
    io = SimpleNamespace(ComfyNode=Node, Schema=lambda **kw: SimpleNamespace(**kw),
                         NodeOutput=NodeOutput, Custom=InputType,
                         Hidden=SimpleNamespace(unique_id="id", extra_pnginfo="metadata"),
                         **{key: InputType(key) for key in ("Int", "Float", "Boolean", "String", "Combo", "Video")})
    api, latest = ModuleType("comfy_api"), ModuleType("comfy_api.latest")
    latest.io = io
    api.latest = latest
    package_name = "comfy_dlss_experimental.nodes"
    package = ModuleType(package_name)
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "comfy_dlss_experimental/nodes")]
    with patch.dict(sys.modules, {"comfy_api": api, "comfy_api.latest": latest, package_name: package}):
        # Avoid using an earlier harness's class objects if this helper is reused.
        for name in list(sys.modules):
            if name.startswith(package_name + "."):
                del sys.modules[name]
        core = importlib.import_module(package_name + ".media_pipeline")
        endpoints = importlib.import_module(package_name + ".pipeline_output")
        yield core, endpoints


class MediaPipelineTests(unittest.TestCase):
    def test_assembly_is_lazy_and_preserves_opaque_video_identity(self):
        original = sequence()
        pipeline = assemble_input(original)
        self.assertIs(pipeline.sequence_copy()["video"], original["video"])
        report = pipeline_report(pipeline)
        self.assertFalse(report["ready_for_execution"])
        self.assertEqual(report["motion"]["usage"], "deferred")
        self.assertEqual(report["validation"]["gpu"], "not_executed")

    def test_override_precedence_and_source_settings_are_unchanged(self):
        original = sequence()
        guides = {"schema_version": 2, "analysis_scale": 75, "flow_provider": provider("dis", preset="fast")}
        pipeline = assemble_input(original, provider("nvidia", output_grid=1), guides)
        effective = guide_settings(pipeline.sequence_copy()["settings"])
        self.assertEqual(effective.motion_provider, "nvidia")
        self.assertEqual(effective.analysis_scale, .75)
        self.assertEqual(effective.flow.output_grid, 1)
        self.assertEqual(original["settings"], {"schema_version": 2, "motion_provider": "dis"})
        self.assertEqual(guides["flow_provider"]["kind"], "dis")

    def test_chained_stages_lower_to_existing_stack_without_encoding(self):
        initial = assemble_input(sequence())
        first = append_nr(initial, look(intensity=.1))
        final = append_nr(first, build_pass_stack(2, look(intensity=.7)))
        source, effect = lower_nr_pipeline(final)
        profiles, metadata = expand_effect(effect)
        self.assertEqual([p["intensity"] for p in profiles], [.1, .7, .7])
        self.assertEqual(metadata["inherited"], [False, False, True])
        self.assertEqual(metadata["intermediate_format"], "rgba8_raw")
        self.assertEqual(source["pipeline_report"]["resolved_motion_provider"], "dis")
        self.assertEqual(len(initial.stages), 0)
        self.assertEqual(len(first.stages), 1)

    def test_branches_and_exported_metadata_do_not_mutate_other_plans(self):
        source = sequence()
        initial = assemble_input(source)
        a, b = append_nr(initial, look(intensity=.1)), append_nr(initial, look(intensity=.9))
        source["public"]["width"] = 128
        seq_a, leaf_a = lower_nr_pipeline(a)
        seq_a["settings"]["analysis_scale"] = 100
        leaf_a["intensity"] = 3
        self.assertEqual(lower_nr_pipeline(a)[1]["intensity"], .1)
        self.assertEqual(lower_nr_pipeline(b)[1]["intensity"], .9)
        self.assertEqual(b.sequence_copy()["public"]["width"], 64)
        self.assertNotIn("analysis_scale", b.sequence_copy()["settings"])

    def test_no_stage_and_more_than_three_fail_explicitly(self):
        initial = assemble_input(sequence())
        with self.assertRaisesRegex(ValueError, "Add a DLSS NR Stage"):
            lower_nr_pipeline(initial)
        final = append_nr(initial, build_pass_stack(3, look()))
        with self.assertRaisesRegex(ValueError, "three total passes"):
            append_nr(final, look())

    def test_all_bypass_removes_estimator_requirement_not_temporal_settings(self):
        initial = assemble_input(sequence(), provider("nvidia"),
                                 {"schema_version": 2, "scene_cut_threshold": .2})
        disabled = append_nr(initial, look(), enabled=False)
        seq, effect = lower_nr_pipeline(disabled)
        self.assertFalse(effect["nr_enabled"])
        self.assertEqual(guide_settings(seq["settings"]).motion_provider, "zero")
        self.assertNotIn("flow_provider", seq["settings"])
        self.assertEqual(seq["settings"]["scene_cut_threshold"], .2)
        # The B bypass must not disable the guides needed by an active A comparison.
        with_a, _ = lower_nr_pipeline(disabled, comparison=look())
        self.assertEqual(guide_settings(with_a["settings"]).motion_provider, "nvidia")
        self.assertEqual(pipeline_report(disabled)["motion"]["usage"], "not_required_bypass")

    def test_mixed_active_stages_keep_selected_motion(self):
        p = assemble_input(sequence(), provider("nvidia"))
        p = append_nr(append_nr(p, look(), False), look())
        self.assertEqual(guide_settings(lower_nr_pipeline(p)[0]["settings"]).motion_provider, "nvidia")

    def test_invalid_inputs_and_profiles_do_not_reach_worker(self):
        for value in (None, {}, sequence() | {"schema_version": True}, sequence() | {"video": None},
                      sequence() | {"public": {"duration": float("nan")}},
                      sequence() | {"public": {"width": True}},
                      sequence() | {"public": None}, sequence() | {"settings": []},
                      sequence() | {"input_report": "invalid"}):
            with self.subTest(value=value), self.assertRaises(ValueError): assemble_input(value)
        with self.assertRaises(ValueError): append_nr(assemble_input(sequence()), look(intensity=float("nan")))
        with self.assertRaises(ValueError): append_nr(assemble_input(sequence()), look(), enabled=1)
        with self.assertRaises(ValueError): lower_nr_pipeline({})
        with self.assertRaisesRegex(ValueError, "settings must be an object"):
            assemble_input(sequence(), settings=[])

    def test_header_failure_is_not_overwritten_by_successful_assembly(self):
        seq = sequence()
        seq["input_report"] = {"ready": False, "state": "unsupported", "issues": [{"message": "HDR"}]}
        report = pipeline_report(append_nr(assemble_input(seq), look()))
        self.assertEqual(report["state"], "blocked")
        self.assertFalse(report["ready_for_execution"])
        self.assertEqual(report["issues"][0]["message"], "HDR")

    def test_extra_guides_check_source_and_are_not_claimed_as_consumed(self):
        seq = sequence()
        depth = GuideAttachment("depth", seq["video"], "relative_inverse_depth", object())
        pipeline = append_nr(assemble_input(seq, attachments=[depth]), look())
        self.assertEqual(pipeline_report(pipeline)["attachments"][0]["usage"], "not_consumed_by_nr")
        with self.assertRaisesRegex(ValueError, "source/active view"):
            assemble_input(sequence(), attachments=[depth])
        with self.assertRaises(ValueError): assemble_input(seq, attachments=[depth, depth])
        with self.assertRaises(ValueError): assemble_input(seq, attachments=[GuideAttachment("motion", seq["video"], "rgb_visualization", object())])

    def test_selector_ignores_invalid_unselected_branches(self):
        self.assertEqual(select_flow("b", a={"bad": True}, b=provider("nvidia"))["kind"], "nvidia")
        with self.assertRaisesRegex(ValueError, "not connected"): select_flow("c", a=provider())
        with self.assertRaises(ValueError): select_flow("auto", a=provider())


class PipelineNodeTests(unittest.TestCase):
    def test_setup_helper_checks_resolved_pipeline_without_starting_render(self):
        with node_modules():
            helper = importlib.import_module("comfy_dlss_experimental.nodes.setup_helper")
            p = append_nr(assemble_input(sequence(), provider("nvidia")), look())
            with patch.object(helper, "inspect_setup", return_value={"ready": True}) as check:
                out = helper.DLSSExperimentalSetupHelper.execute({"runtime": True},
                    sequence=sequence(), flow_provider=provider("dis"), pipeline=p)
                self.assertEqual(guide_settings(check.call_args.kwargs["sequence"]["settings"]).motion_provider, "nvidia")
                self.assertIsNone(check.call_args.kwargs["flow_provider"])
                self.assertFalse(check.call_args.kwargs["verify_nvidia_flow"])
                self.assertTrue(out.result[1])
                self.assertIn("input_pipeline", json.loads(out.ui["dlss_setup_report"][0]))

    def test_selector_uses_comfy_lazy_inputs(self):
        with node_modules() as (nodes, _):
            cls = nodes.DLSSExperimentalFlowSelector
            self.assertEqual(cls.check_lazy_status("b", a=provider()), ["b"])
            self.assertEqual(cls.check_lazy_status("b", b=provider()), [])
            self.assertTrue(all(inp.lazy for inp in cls.define_schema().inputs[1:]))
            self.assertEqual(cls.execute("b", a={}, b=provider("nvidia")).result[0]["kind"], "nvidia")

    def test_new_graph_endpoints_pass_original_video_and_nr_to_existing_execution(self):
        with node_modules() as (nodes, endpoints):
            seq = sequence()
            assembled = nodes.DLSSExperimentalInputAssembler.execute(seq).result[0]
            staged = nodes.DLSSExperimentalNRStage.execute(assembled, look(intensity=.3)).result[0]
            common = {"runtime": {"ready": True}, "start_time": 0, "duration": 3}
            server = SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(client_id="client")))
            with patch.dict(sys.modules, {"server": server}), \
                    patch("comfy_dlss_experimental.nodes.preview_session.run_preview", return_value=({"state": "ready"}, "B", "A")) as preview:
                out = endpoints.DLSSExperimentalPipelinePreview.execute(staged, preview_scale=50, **common)
                args = preview.call_args.kwargs
                self.assertIs(args["sequence"]["video"], seq["video"])
                self.assertEqual(args["profile_b"]["intensity"], .3)
                self.assertEqual(args["preview_mode"], "frame")
                self.assertEqual(args["node_id"], "7")
                self.assertEqual(out.result[2:], ("B", "A"))
            with patch("comfy_dlss_experimental.nodes.process_video.run_process", return_value=("VIDEO", {"passed": True})) as render:
                out = endpoints.DLSSExperimentalPipelineRender.execute(staged, scale=100, process_to_end=True, **common)
                self.assertIs(render.call_args.kwargs["sequence"]["video"], seq["video"])
                self.assertEqual(render.call_args.kwargs["scale"], 100)
                self.assertTrue(render.call_args.kwargs["process_to_end"])
                self.assertEqual(out.result[0], "VIDEO")

    def test_check_plan_nodes_are_output_nodes_without_render_side_effects(self):
        with node_modules() as (nodes, _):
            self.assertTrue(nodes.DLSSExperimentalInputAssembler.define_schema().is_output_node)
            self.assertTrue(nodes.DLSSExperimentalNRStage.define_schema().is_output_node)
            result = nodes.DLSSExperimentalInputAssembler.execute(sequence())
            self.assertEqual(json.loads(result.ui["dlss_pipeline_report"][0])["validation"]["gpu"], "not_executed")


if __name__ == "__main__":
    unittest.main()
