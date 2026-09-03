from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.media_clip import ClipRequest, export_video, file_hash, prepare_clip, probe_media, validate_cfr


class ClipTests(unittest.TestCase):
    def test_limits(self):
        for request in (ClipRequest(duration=86401), ClipRequest(start=-1), ClipRequest(scale=2),
                        ClipRequest(pre_roll=float("nan")), ClipRequest(start=True)):
            with self.assertRaises(ValueError):
                request.validate()
        ClipRequest(duration=100).validate()

    def test_cfr_timestamp_rounding(self):
        rate = Fraction(24000, 1001)
        timestamps = [round(Fraction(i * 10 ** 9, 1) / rate) for i in range(100)]
        validate_cfr(timestamps, rate)

    def test_missing_duplicate_and_discontinuous_timing(self):
        for timestamps in ([], [0, 0], [0, 41_666_667, 100_000_000]):
            with self.assertRaises(ValueError):
                validate_cfr(timestamps, Fraction(24))

    def test_unsupported_colors_and_rotation_fail_closed(self):
        base = {"codec_type": "video", "color_space": "bt709", "color_transfer": "iec61966-2-1",
                "color_primaries": "bt709", "color_range": "tv", "avg_frame_rate": "24/1"}
        for change in ({"color_transfer": "smpte2084"}, {"color_space": "unknown"},
                       {"sample_aspect_ratio": "2:1"}, {"side_data_list": [{"rotation": 90}]}):
            with self.subTest(change=change), tempfile.NamedTemporaryFile() as source:
                result = subprocess.CompletedProcess([], 0, stdout=json.dumps({"streams": [base | change]}))
                with patch("comfy_dlss_experimental.media_clip.subprocess.run", return_value=result):
                    with self.assertRaises(ValueError):
                        probe_media(Path(source.name))


HAS_MEDIA = all(importlib.util.find_spec(name) for name in ("numpy", "cv2", "av")) and bool(shutil.which("ffmpeg"))


@unittest.skipUnless(HAS_MEDIA, "media round-trip requires existing Linux media dependencies")
class MediaRoundTripTests(unittest.TestCase):
    def test_range_preroll_color_audio_and_cancel(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=128x96:rate=24:duration=2",
                            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=32000:duration=2",
                            "-vf", "setparams=range=limited:color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709",
                            "-c:v", "libx264", "-threads", "2", "-crf", "12", "-c:a", "aac", "-colorspace", "bt709",
                            "-color_trc", "iec61966-2-1", "-color_primaries", "bt709", "-color_range", "tv", str(source)],
                           capture_output=True, check=True, timeout=30)
            before = file_hash(source)
            directory = root / "prepared"
            manifest = prepare_clip(source, directory, ClipRequest(start=0.5, duration=0.5, pre_roll=0.25))
            self.assertEqual(len(manifest["frames"]), 18)
            self.assertEqual(manifest["visible_start_index"], 6)
            self.assertEqual(manifest["visible_count"], 12)
            plane = 128 * 96 * 4
            self.assertEqual((directory / "motion.rg16f").stat().st_size, plane * 18)
            raw = (directory / "color.rgba").read_bytes()[plane * 6:]
            self.assertEqual(manifest["frames"][6]["pts_ns"], 500_000_000)
            independent = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(source),
                                           "-frames:v", "1", "-vf", "scale=in_color_matrix=bt709:in_range=limited:out_range=full",
                                           "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1"],
                                          capture_output=True, check=True, timeout=30).stdout
            error = np.abs(np.frombuffer(independent, np.uint8).astype(np.int16) - np.frombuffer(raw[:plane], np.uint8).astype(np.int16))
            self.assertLess(float(error.mean()), 1.5)
            visible = root / "visible.rgba"
            visible.write_bytes(raw)
            output = root / "output.mp4"
            export_video(visible, output, manifest)
            metadata = probe_media(output)
            self.assertTrue(metadata["has_audio"])
            self.assertAlmostEqual(float(metadata["video"]["duration"]), 0.5, places=3)
            self.assertEqual(metadata["video"]["color_transfer"], "iec61966-2-1")
            with self.assertRaises(FileExistsError):
                export_video(visible, output, manifest)
            with self.assertRaises(InterruptedError):
                prepare_clip(source, root / "cancelled", ClipRequest(), cancelled=lambda: True)
            self.assertEqual(file_hash(source), before)
