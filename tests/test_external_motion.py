import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.external_guides import load_external_guide
from comfy_dlss_experimental.external_motion import ExternalMotionReader
from comfy_dlss_experimental.media_pipeline import assemble_input, append_nr, lower_nr_pipeline, pipeline_report
from comfy_dlss_experimental.temporal_guides import GuideSettings
from comfy_dlss_experimental.video_pipeline import guide_settings

ROOT = Path(__file__).resolve().parents[1]
HAS_NUMPY = importlib.util.find_spec("numpy") is not None


class ExternalMotionIntegrationTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "tmp" / "external-motion-tests"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.video = object()
        self.sha = "a" * 64
        self.sequence = {"schema_version": 2, "video": self.video,
                         "settings": {"schema_version": 2, "motion_provider": "dis"},
                         "public": {"width": 64, "height": 64, "duration": 1},
                         "input_report": {"ready": True, "state": "ready", "issues": []}}
        raw = struct.pack("<2f", -6, 4) * (64 * 64)
        (self.root / "motion.raw").write_bytes(raw)
        self.manifest = {
            "schema_version": 1,
            "source": {"sha256": self.sha, "view_id": "source", "width": 64, "height": 64,
                       "time_origin": "video_stream_start"},
            "role": "motion", "semantics": "current_to_previous_pixels_top_left_xy", "units": "input_pixels",
            "grid": {"width": 64, "height": 64, "sampling": "pixel_centers"},
            "dtype": "float32_le", "storage": "raw",
            "metadata": {"includes_camera_motion": True, "includes_jitter": False},
            "frames": [{"path": "motion.raw", "sha256": hashlib.sha256(raw).hexdigest(), "pts_ns": n}
                       for n in (0, 33_333_333, 66_666_667)],
        }
        self.path = self.root / "guide.json"
        self.path.write_text(json.dumps(self.manifest))
        self.provider = load_external_guide(self.path, source_video=self.video)
        self.identity = {"sha256": self.sha, "width": 64, "height": 64}

    def test_assembly_binds_only_selected_motion_and_keeps_video(self):
        configured = assemble_input(self.sequence, external_motion=object())
        self.assertEqual(guide_settings(configured.sequence_copy()["settings"]).motion_provider, "dis")
        with patch.object(type(self.provider), "read_frame", side_effect=AssertionError("No eager frame reads")):
            external = assemble_input(self.sequence, external_motion=self.provider, motion_source="external")
            self.assertIs(external.sequence_copy()["video"], self.video)
            parsed = guide_settings(external.sequence_copy()["settings"])
            self.assertEqual(parsed.motion_provider, "external")
            self.assertIsNone(parsed.flow)
            self.assertEqual(parsed.external_motion, self.provider.snapshot_config())
            report = pipeline_report(append_nr(external, {"schema_version": 2, "nr_enabled": True}))
            self.assertEqual(report["attachments"][0]["usage"], "required")
            self.assertEqual(self.sequence["settings"]["motion_provider"], "dis")

    def test_all_bypass_skips_external_reader_and_clears_recipe(self):
        pipeline = assemble_input(self.sequence, external_motion=self.provider, motion_source="external")
        with patch.object(type(self.provider), "read_frame", side_effect=AssertionError("Bypass reads no guides")):
            sequence, _ = lower_nr_pipeline(append_nr(pipeline, {"schema_version": 2, "nr_enabled": False}))
        self.assertEqual(guide_settings(sequence["settings"]).motion_provider, "zero")
        self.assertNotIn("external_motion", sequence["settings"])

    def test_manifest_change_and_source_mismatch_fail_before_numerical_loading(self):
        config = self.provider.snapshot_config()
        with self.assertRaisesRegex(ValueError, "source content"):
            ExternalMotionReader(config, 64, 64, {**self.identity, "sha256": "b" * 64})
        self.manifest["metadata"]["includes_jitter"] = True
        self.path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "changed"):
            ExternalMotionReader(config, 64, 64, self.identity)

    def test_external_recipe_cannot_be_silently_paired_with_dis(self):
        with self.assertRaises(ValueError):
            GuideSettings(motion_provider="dis", external_motion=self.provider.snapshot_config()).validate()
        with self.assertRaises(ValueError):
            assemble_input(self.sequence, motion_source="external")

    def test_timestamp_tolerance_rejects_a_nearby_frame_after_exact_match(self):
        self.manifest["frames"][1]["pts_ns"] = 500
        self.path.write_text(json.dumps(self.manifest))
        provider = load_external_guide(self.path, source_video=self.video)
        with self.assertRaisesRegex(ValueError, "unique matching timestamp"):
            provider.read_at(0, source_sha256=self.sha)

    @unittest.skipUnless(HAS_NUMPY, "NumPy required for numerical frame reads")
    def test_reader_selects_trimmed_pts_not_local_frame_index(self):
        import numpy as np
        reader = ExternalMotionReader(self.provider.snapshot_config(), 64, 64, self.identity)
        values, info = reader.read(33_333_333)
        np.testing.assert_array_equal(values[5, 5], [-6, 4])
        self.assertEqual(info["frame_index"], 1)
        self.assertFalse(info["reset"])
        self.assertEqual(info["conversion"], "none")
        with self.assertRaisesRegex(ValueError, "increasing"):
            reader.read(33_333_333)
        with self.assertRaisesRegex(ValueError, "timestamp"):
            reader.read(50_000_000)

    @unittest.skipUnless(HAS_NUMPY, "NumPy required for numerical frame reads")
    def test_read_rejects_changed_plane_without_modifying_manifest(self):
        reader = ExternalMotionReader(self.provider.snapshot_config(), 64, 64, self.identity)
        (self.root / "motion.raw").write_bytes(bytes(64 * 64 * 8))
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            reader.read(0)


if __name__ == "__main__":
    unittest.main()
