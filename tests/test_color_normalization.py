from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.color_normalization import (SRGB, color_plan, convert_value, convert_rgba8,
                                                        transfer_lut, export_transfer_filter)
from comfy_dlss_experimental.input_policy import InputColorPolicy, analyze_media
from comfy_dlss_experimental.media_clip import ClipRequest, export_video, file_hash, prepare_clip, probe_media
from comfy_dlss_experimental.temporal_guides import GuideSettings
from comfy_dlss_experimental.video_pipeline import prepared_cache


def tags(transfer="bt709"):
    return {"codec_type": "video", "width": 128, "height": 96, "avg_frame_rate": "24/1", "r_frame_rate": "24/1",
            "color_primaries": "bt709", "color_transfer": transfer, "color_space": "bt709", "color_range": "tv"}


class ColorPolicyTests(unittest.TestCase):
    def test_old_payload_defaults_off_and_no_override(self):
        self.assertFalse(InputColorPolicy.from_dict({"mode": "strict"}).normalize_to_srgb)
        for value in (1, "true", None):
            with self.assertRaises(ValueError):
                InputColorPolicy(normalize_to_srgb=value).validate()
        raw = {"streams": [tags()]}
        before = deepcopy(raw)
        report = analyze_media(raw, InputColorPolicy(normalize_to_srgb=True))
        plan = report["color_normalization"]
        self.assertEqual(plan["operation"], "bt709_to_srgb")
        self.assertEqual(plan["working_transfer"], SRGB)
        self.assertEqual(plan["output_transfer"], "bt709")
        self.assertEqual(report["effective_color"]["color_transfer"], "bt709")
        self.assertEqual(raw, before)

    def test_srgb_is_identity_and_unknown_hdr_not_implicitly_converted(self):
        report = analyze_media({"streams": [tags(SRGB)]}, InputColorPolicy(normalize_to_srgb=True))
        self.assertEqual(report["color_normalization"]["operation"], "already_srgb")
        for transfer in ("unknown", "smpte2084", "arib-std-b67"):
            report = analyze_media({"streams": [tags(transfer)]}, InputColorPolicy(normalize_to_srgb=True))
            self.assertFalse(report["ready"])
            self.assertEqual(report["color_normalization"]["operation"], "blocked")
            self.assertIsNone(report["color_normalization"]["working_transfer"])
        video = tags(); video["side_data_list"] = [{"side_data_type": "Mastering display metadata"}]
        report = analyze_media({"streams": [video]}, InputColorPolicy("fill_missing", normalize_to_srgb=True))
        self.assertEqual(report["color_normalization"]["operation"], "blocked")

    def test_explicit_assumption_is_separate_from_conversion(self):
        video = tags("unknown")
        report = analyze_media({"streams": [video]}, InputColorPolicy("fill_missing", normalize_to_srgb=True))
        self.assertTrue(report["ready"])
        self.assertEqual(report["source_video"]["color_transfer"], "unknown")
        self.assertEqual(report["assumptions"][0]["interpreted_as"], "bt709")
        self.assertEqual(report["color_normalization"]["operation"], "bt709_to_srgb")

    def test_normative_transfer_vectors_and_identity(self):
        # Values from the specified OETF equations, with a known linear 18% gray.
        self.assertAlmostEqual(convert_value(1.099 * .18 ** .45 - .099, "bt709", SRGB), .46135612950044164, places=12)
        self.assertAlmostEqual(convert_value(4.5 * .0031308, "bt709", SRGB), 12.92 * .0031308, places=12)
        for value in (0, .01, .5, 1):
            self.assertEqual(convert_value(value, SRGB, SRGB), value)
        for args in ((float("nan"), SRGB, "bt709"), (-.1, SRGB, "bt709"), (.5, "unknown", SRGB)):
            with self.assertRaises(ValueError):
                convert_value(*args)

    def test_lut_monotonic_endpoints_and_roundtrip_error(self):
        first, back = transfer_lut("bt709", SRGB), transfer_lut(SRGB, "bt709")
        self.assertEqual((first[0], first[-1], back[0], back[-1]), (0, 255, 0, 255))
        self.assertEqual(list(first), sorted(first))
        self.assertEqual(list(back), sorted(back))
        self.assertLessEqual(max(abs(back[first[i]] - i) for i in range(256)), 1)
        self.assertGreater(first[128], 128)  # Actual re-encoding, not tag-only.

    def test_export_plan_validation_and_legacy(self):
        self.assertEqual(export_transfer_filter(None), "")
        self.assertEqual(export_transfer_filter(color_plan(tags(), False)), "")
        self.assertEqual(export_transfer_filter(color_plan(tags(SRGB), True)), "")
        with self.assertRaises(ValueError):
            export_transfer_filter(color_plan(tags("smpte2084"), True))

    def test_cache_reuses_prepared_color_without_nr_look_key(self):
        def prepare(_source, directory, _request, _guides, **_kwargs):
            directory.mkdir(parents=True)
            for name in ("color.rgba", "motion.rg16f"):
                (directory / name).write_bytes(bytes(64 * 64 * 4))
            return {"width": 64, "height": 64, "frames": [{}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); video = root / "source"
            video.write_bytes(b"fixture")
            with patch("comfy_dlss_experimental.video_pipeline.prepare_clip", side_effect=prepare) as conversion:
                def cached(policy, request=ClipRequest()):
                    return prepared_cache(video, request, GuideSettings(), root, lambda: False, lambda *_: None, color_policy=policy)
                off = InputColorPolicy()
                on = replace(off, normalize_to_srgb=True)
                first = cached(off)[0]
                normalized = cached(on)[0]
                self.assertNotEqual(first, normalized)
                for _look_strength in (.25, .5, .75):
                    self.assertTrue(cached(on)[2])  # Effect parameters never enter this API/key.
                self.assertEqual(conversion.call_count, 2)
                with patch("comfy_dlss_experimental.video_pipeline.media_tool_identity", return_value={"version": "changed"}):
                    self.assertFalse(cached(on)[2])
                    self.assertTrue(cached(on)[2])
                    self.assertEqual(conversion.call_count, 3)
                self.assertTrue(cached(on)[2])  # Original tool identity still has its own entry.
                self.assertFalse(cached(on, ClipRequest(scale=.5))[2])
                video.write_bytes(b"changed")
                self.assertFalse(cached(on)[2])


HAS_NUMPY = importlib.util.find_spec("numpy") is not None
HAS_MEDIA = HAS_NUMPY and all(importlib.util.find_spec(name) for name in ("av", "cv2")) and bool(shutil.which("ffmpeg"))


@unittest.skipUnless(HAS_NUMPY, "numpy required")
class PixelTests(unittest.TestCase):
    def test_alpha_preserved_identity_noop_and_malformed_frame(self):
        rgba = bytes([0, 32, 128, 3, 255, 128, 16, 210])
        result = convert_rgba8(rgba, "bt709", SRGB)
        self.assertNotEqual(result, rgba)
        self.assertEqual(result[3::4], rgba[3::4])
        self.assertIs(convert_rgba8(rgba, SRGB, SRGB), rgba)
        with self.assertRaises(ValueError):
            convert_rgba8(rgba[:-1], "bt709", SRGB)


@unittest.skipUnless(HAS_MEDIA, "existing Linux media environment required")
class ColorMediaTests(unittest.TestCase):
    def test_ffmpeg_inverse_matches_python_lut(self):
        import numpy as np
        ramp = np.tile(np.arange(256, dtype=np.uint8), (64, 1))
        pixels = np.stack([ramp, np.roll(ramp, 19, axis=1), np.roll(ramp, 57, axis=1), np.full_like(ramp, 255)], axis=2)
        raw = pixels.tobytes()
        filt = export_transfer_filter(color_plan(tags(), True)).rstrip(",")
        result = subprocess.run(["ffmpeg", "-v", "error", "-filter_threads", "1", "-f", "rawvideo", "-pix_fmt", "rgba",
                                 "-s", "256x64", "-i", "pipe:0", "-vf", filt, "-frames:v", "1", "-pix_fmt", "rgba",
                                 "-f", "rawvideo", "pipe:1"], input=raw, capture_output=True, timeout=30, check=True)
        self.assertEqual(result.stdout, convert_rgba8(raw, SRGB, "bt709"))

    def test_real_preparation_export_and_cache_toggle(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for transfer in ("bt709", SRGB):
                video = root / (transfer + ".mp4")
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=128x96:rate=24:duration=1",
                                "-vf", f"setparams=range=limited:color_primaries=bt709:color_trc={transfer}:colorspace=bt709",
                                "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", str(video)],
                               check=True, capture_output=True, timeout=30)
                digest = file_hash(video)
                request, guides = ClipRequest(0, .5, 0), GuideSettings(motion_provider="zero")
                def prepare(policy):
                    return prepared_cache(video, request, guides, root / "cache", lambda: False, lambda *_: None, color_policy=policy)
                normal, manifest, _ = prepare(InputColorPolicy())
                adapted, converted, reused = prepare(InputColorPolicy(normalize_to_srgb=True))
                self.assertNotEqual(adapted, normal)
                self.assertFalse(reused)
                self.assertTrue(prepare(InputColorPolicy(normalize_to_srgb=True))[2])
                original = (normal / "color.rgba").read_bytes()
                working = (adapted / "color.rgba").read_bytes()
                self.assertEqual(working, convert_rgba8(original, transfer, SRGB))
                self.assertEqual(converted["metadata"]["video"]["color_transfer"], transfer)
                self.assertEqual(converted["color_pipeline"]["working_transfer"], SRGB)
                if transfer == SRGB:
                    self.assertEqual(original, working)
                else:
                    self.assertNotEqual(original, working)
                output = root / (transfer + "-normalized.mp4")
                export_video(adapted / "color.rgba", output, converted)
                metadata = probe_media(output)
                self.assertEqual(metadata["video"]["color_transfer"], transfer)
                self.assertEqual(metadata["fps"], "24")
                baseline = root / (transfer + "-baseline.mp4")
                export_video(normal / "color.rgba", baseline, manifest)
                def decode(path):
                    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1"],
                                          check=True, capture_output=True, timeout=30).stdout
                error = np.abs(np.frombuffer(decode(output), np.uint8).astype(np.int16) - np.frombuffer(decode(baseline), np.uint8).astype(np.int16))
                self.assertLess(float(error.mean()), 2)
                self.assertEqual(file_hash(video), digest)

                # One output frame, but real earlier frames retained for history.
                frame_path, frame_manifest, cached = prepared_cache(video, ClipRequest(.5, .1, .25, 1, True), guides,
                    root / "frame-cache", lambda: False, lambda *_: None)
                self.assertEqual(frame_manifest["visible_count"], 1)
                self.assertGreater(frame_manifest["visible_start_index"], 0)
                self.assertEqual(frame_manifest["frames"][-1]["pts_ns"], 500_000_000)
                self.assertFalse(cached)
                self.assertTrue(prepared_cache(video, ClipRequest(.5, .1, .25, 1, True), guides,
                    root / "frame-cache", lambda: False, lambda *_: None)[2])


if __name__ == "__main__":
    unittest.main()
