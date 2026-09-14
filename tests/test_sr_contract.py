import importlib
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.sr_contract import (
    SRFrameMetadata, SRGuide, SRSettings, plan_sr,
)


def guides(width=640, height=360):
    return (
        SRGuide("color", width, height, "rgba16f_le", "declared_color_space", "source_pixels", color_transfer="srgb"),
        SRGuide("motion", width, height, "rg16f_le", "current_to_previous_input_pixels_top_left_xy", "image_estimate"),
        SRGuide("depth", width, height, "r32f_le", "device_depth_0_1", "render_device_depth", depth_inverted=False),
    )


def numerical_provider(source_video, role, *, reversed_z=False, includes_jitter=False, second_pts=33_333_333):
    from comfy_dlss_experimental.external_guides import ExternalGuideProvider, GuideFile
    motion = role == "motion"
    metadata = {"includes_camera_motion": True, "includes_jitter": includes_jitter} if motion else {
        "near": .1, "far": 100, "projection": "perspective", "reversed_z": reversed_z}
    return ExternalGuideProvider(
        manifest_path=Path("never-open-this-manifest.json"), source_video=source_video,
        source_sha256="a" * 64, view_id="source", source_width=640, source_height=360,
        role=role, semantics="current_to_previous_pixels_top_left_xy" if motion else "device_z",
        units="input_pixels" if motion else "zero_to_one", width=640, height=360,
        channels=2 if motion else 1, dtype="float32_le", storage="raw",
        files=(GuideFile("first.raw", "b" * 64, 0), GuideFile("second.raw", "c" * 64, second_pts)),
        metadata_json=json.dumps(metadata), cache_identity=("d" if motion else "e") * 64,
    )


class SRContractTests(unittest.TestCase):
    def test_official_enum_and_explicit_extent_mapping(self):
        for mode, expected in (("quality", 2), ("balanced", 1), ("performance", 0), ("ultra_performance", 3)):
            with self.subTest(mode=mode):
                plan = plan_sr(SRSettings(mode=mode, preset="k"), 640, 360, 1280, 720)
                self.assertEqual(plan["native_settings"]["mode"], expected)
                self.assertEqual(plan["native_settings"]["preset"], 11)
                self.assertEqual(plan["output"]["pixels"], 1280 * 720)
                self.assertEqual(plan["input"]["inverse_dimensions"], [1 / 640, 1 / 360])
                self.assertEqual(plan["raw_plane_bytes_per_frame"]["total"], 640 * 360 * 16 + 1280 * 720 * 8)
                self.assertFalse(plan["runnable"])

    def test_dlaa_same_extent_without_fake_scale_or_jitter(self):
        report = plan_sr(SRSettings(mode="dlaa"), 640, 360)
        self.assertEqual(report["native_settings"]["mode"], 5)
        self.assertEqual(report["input"], report["output"])
        self.assertFalse(report["jitter"]["synthetic_jitter"])
        self.assertTrue(any("zero jitter" in text for text in report["warnings"]))

    def test_dimensions_are_bounded_exact_and_mode_specific(self):
        cases = [({}, (640, 360)), ({}, (640, 360, 640, 360)),
                 ({}, (640, 360, 1280, 721)), ({}, (1921, 1080, 3840, 2160)),
                 ({}, (True, 360, 1280, 720)), ({}, (640.0, 360, 1280, 720)),
                 ({}, (640, 360, 7680, 4320)), ({}, (640, 360, 320, 180)),
                 ({"mode": "dlaa"}, (640, 360, 1280, 720))]
        for settings, extents in cases:
            with self.subTest(extents=extents), self.assertRaises(ValueError):
                plan_sr(SRSettings(**settings), *extents)

    def test_invalid_settings_and_payloads_fail_closed(self):
        for values in ({"mode": "ultra_quality"}, {"mode": 2}, {"mode": []}, {"preset": 0},
                       {"hdr": 1}, {"depth_inverted": "false"}, {"motion_jittered": True},
                       {"jitter_policy": "synthetic_halton"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                SRSettings(**values)
        for value in (None, {}, {"schema_version": True}, {"schema_version": 1, "surprise": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SRSettings.from_payload(value)
        settings = SRSettings(mode="performance")
        self.assertEqual(SRSettings.from_payload(settings.to_payload()), settings)

    def test_complete_metadata_is_never_an_execution_claim(self):
        report = plan_sr(SRSettings(), 640, 360, 1280, 720, guides=guides())
        self.assertTrue(report["metadata_complete"])
        self.assertEqual(report["missing_inputs"], [])
        self.assertFalse(report["runnable"])
        self.assertEqual(report["validation"]["gpu"], "not_executed")
        self.assertTrue(any("Optical flow" in text for text in report["warnings"]))
        self.assertIn("SDK-enabled CSR1 Worker", report["blocked_reason"])

    def test_depth_is_not_relative_depth_or_an_automatic_conversion(self):
        for semantics, provenance in (("relative_inverse_depth", "image_estimate"),
                                      ("metric_depth", "image_estimate"),
                                      ("device_depth_0_1", "normalized_monocular_depth")):
            with self.subTest(semantics=semantics), self.assertRaises(ValueError):
                SRGuide("depth", 640, 360, "r32f_le", semantics, provenance, depth_inverted=False)
        with self.assertRaisesRegex(ValueError, "encoding"):
            plan_sr(SRSettings(depth_inverted=True), 640, 360, 1280, 720, guides=guides())
        projected = SRGuide("depth", 640, 360, "r32f_le", "device_depth_0_1",
                            "calibrated_projection_estimate", depth_inverted=False)
        report = plan_sr(SRSettings(), 640, 360, 1280, 720, guides=[projected])
        self.assertTrue(any("estimated geometry" in text for text in report["warnings"]))

    def test_guides_require_unique_role_matching_extents_and_alignment(self):
        for supplied in ((guides()[0], guides()[0]), guides(1280, 720), ({"role": "depth"},)):
            with self.subTest(supplied=supplied), self.assertRaises(ValueError):
                plan_sr(SRSettings(), 640, 360, 1280, 720, guides=supplied)
        with self.assertRaisesRegex(ValueError, "same source frame"):
            SRGuide("color", 640, 360, "rgba16f_le", "declared_color_space", "source_pixels", frame_alignment="next_frame")
        with self.assertRaises(ValueError):
            SRGuide("motion", 640, 360, "rgb8", "visualized_flow", "image_estimate")

    def test_color_transfer_is_explicit_and_must_match_hdr(self):
        with self.assertRaisesRegex(ValueError, "explicitly declare"):
            SRGuide("color", 640, 360, "rgba16f_le", "declared_color_space", "source_pixels")
        with self.assertRaisesRegex(ValueError, "Color transfer"):
            plan_sr(SRSettings(hdr=True), 640, 360, 1280, 720, guides=guides())
        color = SRGuide("color", 640, 360, "rgba16f_le", "declared_color_space", "source_pixels", color_transfer="linear_hdr")
        report = plan_sr(SRSettings(hdr=True), 640, 360, 1280, 720, guides=[color])
        self.assertTrue(any("not an HDR reconstruction path" in text for text in report["warnings"]))

    def test_manual_exposure_and_external_jitter_are_explicit_requirements(self):
        settings = SRSettings(auto_exposure=False, jitter_policy="external_render_metadata")
        report = plan_sr(settings, 640, 360, 1280, 720, guides=guides())
        self.assertEqual(report["missing_inputs"], ["actual_jitter_metadata", "positive_exposure_metadata"])
        supplied = plan_sr(settings, 640, 360, 1280, 720, guides=guides(),
                           frame_metadata=SRFrameMetadata(jitter_x=.25), external_jitter_available=True)
        self.assertTrue(supplied["metadata_complete"])
        self.assertFalse(supplied["runnable"])
        with self.assertRaisesRegex(ValueError, "invent"):
            plan_sr(SRSettings(), 640, 360, 1280, 720, frame_metadata=SRFrameMetadata(jitter_x=.25))

    def test_frame_values_are_finite_positive_and_not_coerced(self):
        for values in ({"exposure": 0}, {"pre_exposure": -1}, {"exposure_scale": math.inf},
                       {"exposure": 65505}, {"exposure": 10 ** 1000}, {"frame_time_ms": 0}, {"frame_time_ms": True},
                       {"frame_time_ms": 60001}, {"motion_scale_x": 0}, {"motion_scale_y": math.nan},
                       {"jitter_y": .6}, {"reset": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                SRFrameMetadata(**values)

    def test_nodes_emit_only_settings_and_an_inspection_report(self):
        from test_media_pipeline import node_modules, sequence
        with node_modules():
            nodes = importlib.import_module("comfy_dlss_experimental.nodes.super_resolution")
            config_schema = nodes.DLSSExperimentalSRSettings.define_schema()
            self.assertEqual(config_schema.node_id, "DLSSExperimentalSRSettings")
            settings = nodes.DLSSExperimentalSRSettings.execute(mode="dlaa").result[0]
            node = nodes.DLSSExperimentalSRPlan
            self.assertEqual(len(node.define_schema().outputs), 1)
            report = json.loads(node.execute(settings, sequence=sequence()).ui["dlss_sr_plan"][0])
            self.assertEqual(report["output"]["width"], 64)
            self.assertEqual(report["source_dimensions"], "inspected_metadata")
            self.assertFalse(report["runnable"])
            with self.assertRaisesRegex(ValueError, "either"):
                node.execute(settings, sequence=sequence(), pipeline=object())

    def test_node_inspects_connected_declarations_without_reading_planes_or_inventing_provenance(self):
        from test_media_pipeline import node_modules, sequence
        from comfy_dlss_experimental.media_pipeline import assemble_input
        from comfy_dlss_experimental.external_guides import ExternalGuideProvider
        source = sequence()
        source["public"].update(width=640, height=360)
        motion, depth = (numerical_provider(source["video"], role) for role in ("motion", "depth"))
        pipeline = assemble_input(source, attachments=[motion.to_attachment(), depth.to_attachment()])
        with node_modules(), patch.object(ExternalGuideProvider, "read_frame", side_effect=AssertionError("no plane reads")), \
                patch.object(Path, "open", side_effect=AssertionError("no manifest/frame reads")):
            nodes = importlib.import_module("comfy_dlss_experimental.nodes.super_resolution")
            report = json.loads(nodes.DLSSExperimentalSRPlan.execute(SRSettings().to_payload(), pipeline=pipeline).ui["dlss_sr_plan"][0])
        self.assertEqual(report["state"], "blocked")
        self.assertEqual(report["missing_inputs"], ["color"])
        self.assertEqual(report["guide_pair_validation"]["state"], "guide_contract_validated")
        self.assertTrue(all(item["sr_compatibility"]["state"] == "declared_compatible" for item in report["attached_guides"]))
        self.assertFalse(report["metadata_complete"])
        self.assertFalse(report["runnable"])
        self.assertEqual(report["guides"], {})  # Never invent SRGuide provenance from a manifest.
        self.assertEqual(report["input_status"]["color"]["state"], "conversion_pending")
        self.assertTrue(any("float32 motion" in text for text in report["pending_preparations"]))

    def test_attached_depth_direction_and_motion_jitter_must_match_settings(self):
        from comfy_dlss_experimental.sr_contract import inspect_sr_attachments
        video = object()
        settings = SRSettings(depth_inverted=True, motion_jittered=True, jitter_policy="external_render_metadata")
        attached = [numerical_provider(video, role).to_attachment() for role in ("motion", "depth")]
        result = inspect_sr_attachments(plan_sr(settings, 640, 360, 1280, 720), settings, {"video": video}, attached)
        self.assertEqual(result["guide_pair_validation"]["state"], "incompatible")
        self.assertIn("includes_jitter", result["input_status"]["motion"]["reason"])
        self.assertIn("reversed_z", result["input_status"]["depth"]["reason"])
        self.assertIn("motion", result["missing_inputs"])
        self.assertIn("depth", result["missing_inputs"])

    def test_guide_pair_timestamp_mismatch_is_a_specific_blocker(self):
        from comfy_dlss_experimental.sr_contract import inspect_sr_attachments
        video = object()
        attached = [numerical_provider(video, "motion").to_attachment(),
                    numerical_provider(video, "depth", second_pts=33_333_334).to_attachment()]
        result = inspect_sr_attachments(plan_sr(SRSettings(), 640, 360, 1280, 720), SRSettings(), {"video": video}, attached)
        self.assertEqual(result["guide_pair_validation"]["state"], "incompatible")
        self.assertIn("timestamps", result["input_status"]["motion"]["reason"])
        self.assertIn("timestamps", result["input_status"]["depth"]["reason"])
        self.assertEqual(result["missing_inputs"], ["color", "motion", "depth"])


if __name__ == "__main__":
    unittest.main()
