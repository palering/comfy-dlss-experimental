import importlib
import json
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.media_pipeline import (
    append_nr, append_sr, assemble_input, lower_nr_pipeline, lower_sr_pipeline, pipeline_report,
)
from comfy_dlss_experimental.sr_contract import SRSettings
from test_media_pipeline import node_modules, sequence, look


class SRPipelineTests(unittest.TestCase):
    def test_stage_keeps_source_identity_and_does_not_claim_gpu_readiness(self):
        seq = sequence()
        original = assemble_input(seq)
        staged = append_sr(original, SRSettings(), 128, 128)
        source, params = lower_sr_pipeline(staged)
        self.assertIs(source.sequence_copy()["video"], seq["video"])
        self.assertEqual(original.stages, ())
        self.assertEqual(source.stages, ())
        self.assertEqual(params["output_width"], 128)
        params["settings"]["mode"] = "performance"
        self.assertEqual(lower_sr_pipeline(staged)[1]["settings"]["mode"], "quality")
        report = pipeline_report(staged)
        self.assertFalse(report["ready_for_execution"])
        self.assertEqual(report["validation"]["gpu"], "not_executed")
        self.assertEqual(report["missing_inputs"], ["depth"])
        self.assertEqual(report["output"], {"width": 128, "height": 128})
        self.assertEqual(staged.stages[0].backend_id, "owned_csr1_sr")

    def test_dlaa_uses_source_extent_and_branches_remain_independent(self):
        original = assemble_input(sequence())
        sr = append_sr(original, SRSettings(), 128, 128)
        dlaa = append_sr(original, SRSettings(mode="dlaa"), 200, 300)
        self.assertEqual(dlaa.stages[0].feature, "dlaa")
        self.assertEqual(lower_sr_pipeline(dlaa)[1]["output_width"], 64)
        self.assertEqual(lower_sr_pipeline(sr)[1]["output_width"], 128)

    def test_planner_distinguishes_configured_motion_from_missing_depth(self):
        from comfy_dlss_experimental.sr_contract import inspect_sr_attachments, plan_sr
        settings = SRSettings()
        report = inspect_sr_attachments(plan_sr(settings, 64, 64, 128, 128), settings, sequence(), ())
        self.assertEqual(report["deferred_inputs"], ["motion"])
        self.assertNotIn("motion", report["missing_inputs"])
        self.assertIn("depth", report["missing_inputs"])
        self.assertFalse(report["runnable"])

    def test_unsupported_composition_and_preview_do_not_silently_run_nr(self):
        source = assemble_input(sequence())
        sr = append_sr(source, SRSettings(), 128, 128)
        nr = append_nr(source, look())
        for action in (lambda: append_sr(sr, SRSettings(), 256, 256),
                       lambda: append_sr(nr, SRSettings(), 128, 128),
                       lambda: append_nr(sr, look()),
                       lambda: lower_sr_pipeline(nr), lambda: lower_nr_pipeline(sr)):
            with self.assertRaises(ValueError): action()
        with self.assertRaises(ValueError): append_sr(source, SRSettings(), 128, 144)

    def test_sr_node_render_dispatch_and_rejections(self):
        with node_modules() as (_, endpoints):
            nodes = importlib.import_module("comfy_dlss_experimental.nodes.super_resolution")
            original = assemble_input(sequence())
            staged = nodes.DLSSExperimentalSRStage.execute(original, SRSettings().to_payload(), 128, 128)
            self.assertTrue(nodes.DLSSExperimentalSRStage.define_schema().is_output_node)
            self.assertIn("dlss_pipeline_report", staged.ui)
            runtime = {"backend": "owned_sr"}
            with patch("comfy_dlss_experimental.sr_execution.run_sr_process", return_value=("SR_VIDEO", {"feature": "sr"})) as render, \
                    patch("comfy_dlss_experimental.nodes.process_video.run_process") as nr:
                result = endpoints.DLSSExperimentalPipelineRender.execute(staged.result[0], runtime=runtime,
                    scale=100, start_time=1, duration=2, process_to_end=False,
                    contract={"schema_version": 2, "mode": "native", "pre_roll": .25, "warmup_frames": 120})
                self.assertEqual(result.result[0], "SR_VIDEO")
                self.assertEqual(json.loads(result.result[1])["feature"], "sr")
                self.assertEqual(render.call_args.kwargs["pipeline"].stages, ())
                self.assertEqual(render.call_args.kwargs["output_width"], 128)
                self.assertEqual(render.call_args.kwargs["pre_roll"], .25)
                self.assertNotIn("warmup_frames", render.call_args.kwargs)
                nr.assert_not_called()
                render.reset_mock()
                for args in ({"scale": 50}, {"contract": {"schema_version": 1}}, {"contract": "bad"}):
                    with self.assertRaises(ValueError):
                        endpoints.DLSSExperimentalPipelineRender.execute(staged.result[0], runtime=runtime, **args)
                with self.assertRaisesRegex(ValueError, "NR only"):
                    endpoints.DLSSExperimentalPipelinePreview.execute(staged.result[0], runtime=runtime)
                render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
