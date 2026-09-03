from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.input_policy import InputColorPolicy, analyze_media, validated_metadata
from comfy_dlss_experimental.media_clip import ClipRequest, inspect_media, prepare_clip, file_hash
from comfy_dlss_experimental.temporal_guides import GuideSettings, TemporalGuideGenerator
from comfy_dlss_experimental.video_pipeline import prepared_cache


def source(tags=True):
    video = {"codec_type": "video", "codec_name": "h264", "width": 1344, "height": 768,
             "avg_frame_rate": "24/1", "r_frame_rate": "24/1", "start_time": "0", "duration": "5.166667"}
    if tags:
        video.update(color_space="bt709", color_primaries="bt709", color_transfer="iec61966-2-1", color_range="tv")
    return {"streams": [video, {"codec_type": "audio", "codec_name": "aac", "start_time": "0"}],
            "format": {"format_name": "mov,mp4", "duration": "5.184"}}


class PolicyTests(unittest.TestCase):
    def test_strict_is_non_destructive_and_lists_all_missing_fields(self):
        raw = source(False); before = deepcopy(raw)
        result = analyze_media(raw)
        self.assertEqual(result["state"], "needs_confirmation")
        self.assertEqual(len(result["issues"]), 4)
        self.assertFalse(result["ready"])
        self.assertEqual(result["source_video"], before["streams"][0])
        self.assertEqual(raw, before)
        with self.assertRaisesRegex(ValueError, "fill_missing"):
            validated_metadata(raw)

    def test_explicit_interpretation_is_recorded_not_written_into_original(self):
        raw = source(False); before = deepcopy(raw)
        result = validated_metadata(raw, InputColorPolicy("fill_missing", "bt709", "tv"))
        self.assertEqual(result["video"]["color_transfer"], "bt709")
        self.assertEqual(result["input_report"]["state"], "ready_with_assumptions")
        self.assertEqual(len(result["input_report"]["assumptions"]), 4)
        self.assertEqual(raw, before)

    def test_known_srgb_is_never_overridden_by_bt709_assumption(self):
        raw = source(); raw["streams"][0].pop("color_range")
        result = validated_metadata(raw, InputColorPolicy("fill_missing", "bt709", "pc"))
        self.assertEqual(result["video"]["color_transfer"], "iec61966-2-1")
        self.assertEqual(result["video"]["color_range"], "pc")
        self.assertEqual([x["field"] for x in result["input_report"]["assumptions"]], ["color_range"])

    def test_known_hdr_and_auxiliary_hdr_metadata_remain_blocked(self):
        for change in ({"color_transfer": "smpte2084"}, {"color_transfer": "arib-std-b67"},
                       {"color_primaries": "bt2020"}, {"side_data_list": [{"side_data_type": "Mastering display metadata"}]}):
            raw = source(False); raw["streams"][0].update(change)
            result = analyze_media(raw, InputColorPolicy("fill_missing"))
            self.assertEqual(result["state"], "unsupported")
            self.assertFalse(result["ready"])

    def test_header_success_is_not_a_cfr_or_decodability_claim(self):
        result = analyze_media(source())
        self.assertTrue(result["ready"])
        self.assertEqual(result["decode_validation"], "not_run")
        self.assertIn("during_preparation", result["timing_validation"])

    def test_geometry_and_unknown_rate_are_explicitly_blocked(self):
        for change in ({"sample_aspect_ratio": "2:1"}, {"side_data_list": [{"rotation": 90}]},
                       {"avg_frame_rate": "0/0"}, {"width": 0}):
            raw = source(); raw["streams"][0].update(change)
            self.assertEqual(analyze_media(raw)["state"], "unsupported")

    def test_audio_offset_is_reported_not_silently_exported(self):
        raw = source(); raw["streams"][1]["start_time"] = "1"
        report = analyze_media(raw)
        self.assertFalse(report["ready"])
        self.assertTrue(any("起始时间" in issue["message"] for issue in report["issues"]))

    def test_no_video_and_invalid_policy(self):
        self.assertFalse(analyze_media({"streams": []})["ready"])
        with self.assertRaises(ValueError):
            analyze_media(source(), InputColorPolicy("override_hdr"))


HAS_MEDIA = all(importlib.util.find_spec(name) for name in ("numpy", "cv2", "av")) and bool(shutil.which("ffmpeg"))


@unittest.skipUnless(HAS_MEDIA, "Requires Linux media dependencies")
class InputMediaTests(unittest.TestCase):
    def test_zero_motion_does_not_construct_dis_and_keeps_scene_detection(self):
        import numpy as np
        with patch("cv2.DISOpticalFlow_create", side_effect=AssertionError("DIS must not be created")):
            generator = TemporalGuideGenerator(64, 64, GuideSettings(motion_provider="zero"))
            black = bytes([0, 0, 0, 255]) * 4096
            white = bytes([255, 255, 255, 255]) * 4096
            generator.process(black)
            flow, report = generator.process(black)
            self.assertFalse(report["reset"])
            self.assertIsNone(report["consistent_fraction"])
            self.assertFalse(np.frombuffer(flow, "<f2").any())
            _, report = generator.process(white)
            self.assertEqual(report["reset_reason"], "scene_cut")

    def test_untagged_decode_cache_is_policy_and_guide_specific(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); video = root / "untagged.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=128x96:rate=24:duration=1",
                            "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", str(video)], check=True, timeout=30)
            digest = file_hash(video)
            self.assertEqual(analyze_media(inspect_media(video))["state"], "needs_confirmation")
            request, guides = ClipRequest(0, .5, 0), GuideSettings(motion_provider="zero")
            def prepare(policy, selected_guides=guides):
                return prepared_cache(video, request, selected_guides, root / "cache", lambda: False, lambda *_: None, color_policy=policy)
            first, manifest, reused = prepare(InputColorPolicy("fill_missing"))
            self.assertFalse(reused)
            self.assertEqual(manifest["visible_count"], 12)
            self.assertFalse(np.frombuffer((first / "motion.rg16f").read_bytes(), "<f2").any())
            self.assertTrue(prepare(InputColorPolicy("fill_missing"))[2])
            other, _, reused = prepare(InputColorPolicy("fill_missing", range="pc"))
            self.assertNotEqual(other, first); self.assertFalse(reused)
            flow, _, reused = prepare(InputColorPolicy("fill_missing"), GuideSettings())
            self.assertNotEqual(flow, first); self.assertFalse(reused)
            self.assertEqual(file_hash(video), digest)
            independent = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-frames:v", "1",
                "-vf", "scale=in_color_matrix=bt709:in_range=limited:out_range=full", "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1"],
                check=True, capture_output=True, timeout=30).stdout
            raw = (first / "color.rgba").read_bytes()[:128*96*4]
            error = np.abs(np.frombuffer(raw, np.uint8).astype(np.int16) - np.frombuffer(independent, np.uint8).astype(np.int16))
            self.assertLess(float(error.mean()), 1.5)
