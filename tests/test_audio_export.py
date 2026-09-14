"""Real FFmpeg timing checks, independent of Comfy and the GPU."""
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from comfy_dlss_experimental.media_clip import export_video
from comfy_dlss_experimental.stream_media import StreamEncoder


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires existing FFmpeg/FFprobe")
class AudioExportTests(unittest.TestCase):
    def test_audio_ends_at_visible_frame_range_for_both_encoders(self):
        parent = Path(__file__).resolve().parents[1] / "tmp/audio-export-tests"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            root = Path(temporary)
            for sample_rate, fps, count, start in ((48000, "30", 1, 0), (48000, "30", 8, 0),
                                                  (44100, "30000/1001", 1, .1)):
                key = f"{sample_rate}-{count}"
                source = root / f"{key}.m4a"
                subprocess.run(["ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i",
                    f"sine=frequency=1000:sample_rate={sample_rate}:duration=1", "-c:a", "aac", str(source)],
                    check=True, capture_output=True, timeout=15)
                duration = float(Fraction(count) / Fraction(fps))
                manifest = {"width": 64, "height": 64, "fps": fps, "visible_count": count,
                    "visible_start_index": 0, "frames": [{"pts_ns": round(start * 1e9)}], "source": str(source),
                    "metadata": {"has_audio": True, "video": {"color_transfer": "bt709"}}}
                frame = bytes([96, 96, 96, 255]) * (64 * 64)
                raw = root / f"{key}.rgba"
                raw.write_bytes(frame * count)
                for mode in ("cached", "streamed"):
                    with self.subTest(sample_rate=sample_rate, fps=fps, count=count, start=start, mode=mode):
                        destination = root / f"{key}-{mode}.mp4"
                        if mode == "cached":
                            export_video(raw, destination, manifest)
                        else:
                            with StreamEncoder(destination, manifest, lambda: False) as encoder:
                                for _ in range(count):
                                    encoder.write(frame)
                                encoder.finish()
                        probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams",
                            "-show_packets", "-of", "json", str(destination)], text=True, timeout=15))
                        video, audio = (next(s for s in probe["streams"] if s["codec_type"] == kind)
                                        for kind in ("video", "audio"))
                        self.assertEqual(video["nb_frames"], str(count))
                        self.assertEqual(int(audio["sample_rate"]), sample_rate)
                        self.assertEqual(audio["codec_name"], "aac")
                        self.assertAlmostEqual(float(video["duration"]), duration, delta=1e-6)
                        self.assertAlmostEqual(float(audio["start_time"]), 0, delta=1 / sample_rate)
                        self.assertAlmostEqual(float(audio["duration"]), duration, delta=1 / sample_rate + 1e-6)
                        end = max(float(p["pts_time"]) + float(p["duration_time"]) for p in probe["packets"]
                                  if p["codec_type"] == "audio")
                        self.assertAlmostEqual(end, duration, delta=1 / sample_rate + 2e-6)
