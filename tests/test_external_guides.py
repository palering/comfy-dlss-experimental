from copy import deepcopy
import hashlib
import importlib.util
import importlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from comfy_dlss_experimental.external_guides import (
    load_external_guide, reopen_external_guide, select_external_guide, validate_sr_guides,
)

HAS_NUMPY = importlib.util.find_spec("numpy") is not None


class ExternalGuideTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "tmp" / "external-guide-tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.video = object()

    def manifest(self, *, role="motion", semantics=None, units=None, metadata=None, values=None, storage="raw"):
        channels = {"motion": 2, "depth": 1, "confidence": 1, "mask": 1, "normals": 3}[role]
        if role == "motion":
            semantics, units, metadata = semantics or "current_to_previous_pixels_top_left_xy", units or "input_pixels", metadata if metadata is not None else {"includes_camera_motion": True, "includes_jitter": False}
        elif role == "depth":
            semantics, units, metadata = semantics or "device_z", units or "zero_to_one", metadata if metadata is not None else {"reversed_z": False, "near": .1, "far": 100, "projection": "perspective"}
        else:
            semantics, units, metadata = semantics or "motion_confidence", units or "zero_to_one", metadata or {}
        data = struct.pack("<" + "f" * (4 * channels), *(values or [.5] * (4 * channels)))
        value = {"schema_version": 1, "source": {"sha256": "a" * 64, "view_id": "source", "width": 2, "height": 2,
                                                  "time_origin": "video_stream_start"},
                 "role": role, "semantics": semantics, "units": units, "grid": {"width": 2, "height": 2, "sampling": "pixel_centers"},
                 "dtype": "float32_le", "storage": storage, "metadata": metadata,
                 "frames": [{"path": "frame.raw", "sha256": hashlib.sha256(data).hexdigest(), "pts_ns": 0},
                            {"path": "missing.raw", "sha256": "0" * 64, "pts_ns": 33_333_333}]}
        (self.root / "frame.raw").write_bytes(data)
        return value

    def load(self, value, name="guide.json"):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf8")
        return load_external_guide(path, source_video=self.video, source_width=2, source_height=2)

    def frame(self, provider, index=0, **kwargs):
        return provider.read_frame(index, source_sha256=kwargs.get("source_sha256", "a" * 64),
                                   view_id=kwargs.get("view_id", "source"), pts_ns=kwargs.get("pts_ns", 0))

    def test_configuration_is_lazy_and_only_selected_frame_is_read(self):
        provider = self.load(self.manifest())
        self.assertIs(deepcopy(provider), provider)
        self.assertIs(provider.source_video, self.video)
        self.assertEqual(len(self.frame(provider).data), 32)
        with self.assertRaises(FileNotFoundError):
            self.frame(provider, 1, pts_ns=33_333_333)
        self.assertEqual(provider.report()["source_info"]["view_id"], "source")
        self.assertEqual(provider.report()["source_info"]["time_origin"], "video_stream_start")
        with patch("pathlib.Path.open", side_effect=AssertionError("selector must not read")):
            self.assertIs(select_external_guide("b", a=object(), b=provider), provider)
        for selection in ("z", "a"):
            with self.assertRaises(ValueError):
                select_external_guide(selection, b=provider)

    def test_exact_source_view_timestamp_and_boundaries(self):
        provider = self.load(self.manifest())
        for args in ({"source_sha256": "b" * 64}, {"view_id": "cropped"}, {"pts_ns": 1}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.frame(provider, **args)
        for index in (-1, 2, True, 1.0):
            with self.assertRaises(ValueError):
                self.frame(provider, index)
        with self.assertRaisesRegex(ValueError, "active VIDEO"):
            load_external_guide(provider.manifest_path, source_video=self.video, source_width=4)

    def test_snapshot_detects_manifest_change_and_frames_are_content_addressed(self):
        value = self.manifest()
        provider = self.load(value)
        reopened = reopen_external_guide(provider.snapshot_config())
        self.assertEqual(reopened.cache_identity, provider.cache_identity)
        value["frames"][1]["reset"] = True
        changed = self.load(value)
        self.assertNotEqual(changed.cache_identity, provider.cache_identity)
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            reopen_external_guide(provider.snapshot_config())
        (self.root / "frame.raw").write_bytes(bytes(32))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.frame(changed)

    def test_malformed_semantics_paths_and_numeric_values_are_rejected(self):
        for key, bad in (("role", "rgb_flow"), ("semantics", []), ("units", []), ("dtype", "uint8"),
                         ("storage", "png"), ("schema_version", True)):
            value = self.manifest()
            value[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load(value)
        for bad in ("../escape", "/escape", "C:\\escape", "a/../b"):
            value = self.manifest()
            value["frames"][0]["path"] = bad
            with self.assertRaises(ValueError): self.load(value)
        for values in ([float("nan")] * 8, [float("inf")] * 8):
            with self.assertRaisesRegex(ValueError, "finite"):
                self.frame(self.load(self.manifest(values=values)))
        with self.assertRaisesRegex(ValueError, r"\[0, 1\]"):
            self.frame(self.load(self.manifest(role="confidence", values=[1.1] * 4)))
        with self.assertRaisesRegex(ValueError, "strictly positive"):
            self.frame(self.load(self.manifest(role="depth", semantics="linear_view_z", units="relative", metadata={}, values=[0] * 4)))

    def test_timestamps_duplicate_keys_sizes_and_reset_flags_are_strict(self):
        value = self.manifest()
        value["source"].pop("time_origin")
        with self.assertRaisesRegex(ValueError, "guide source"):
            self.load(value)
        for wrong in ("container_pts", "absolute", "unix"):
            value = self.manifest()
            value["source"]["time_origin"] = wrong
            with self.assertRaisesRegex(ValueError, "stream.start_time"):
                self.load(value)
        for field, bad in (("pts_ns", 0), ("pts_ns", float("nan")), ("reset", 1)):
            value = self.manifest()
            value["frames"][1][field] = bad
            with self.assertRaises(ValueError): self.load(value)
        value = self.manifest()
        value["frames"][0]["sha256"] = hashlib.sha256(b"bad").hexdigest()
        provider = self.load(value)
        (self.root / "frame.raw").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "byte length"):
            self.frame(provider)
        (self.root / "duplicate.json").write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_external_guide(self.root / "duplicate.json", source_video=self.video)

    def test_symlinks_cannot_escape_manifest_root(self):
        child = self.root / "child"
        child.mkdir()
        value = self.manifest()
        (child / "frame.raw").symlink_to(self.root / "frame.raw")
        path = child / "guide.json"
        path.write_text(json.dumps(value))
        provider = load_external_guide(path, source_video=self.video)
        with self.assertRaisesRegex(ValueError, "outside"):
            self.frame(provider)

    def test_nr_contract_does_not_invert_or_invent_camera_motion(self):
        for change in ({"semantics": "previous_to_current_pixels_top_left_xy"},
                       {"source": {"sha256": "a" * 64, "view_id": "crop", "width": 2, "height": 2,
                                   "time_origin": "video_stream_start"}},
                       {"grid": {"width": 1, "height": 1, "sampling": "pixel_centers"}},
                       {"metadata": {"includes_camera_motion": False, "includes_jitter": False}},
                       {"metadata": {"includes_camera_motion": True, "includes_jitter": True}}):
            value = self.manifest() | change
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load(value).validate_nr_motion()
        self.load(self.manifest()).validate_nr_motion()

    def test_sr_depth_is_not_confused_with_relative_monocular_depth(self):
        motion = self.load(self.manifest(), "motion.json")
        depth = self.load(self.manifest(role="depth"), "depth.json")
        self.assertEqual(validate_sr_guides(motion, depth)["state"], "guide_contract_validated")
        relative = self.load(self.manifest(role="depth", semantics="linear_view_z", units="relative", metadata={}))
        with self.assertRaisesRegex(ValueError, "projection conversion"):
            validate_sr_guides(motion, relative)
        other_video = load_external_guide(depth.manifest_path, source_video=object())
        with self.assertRaisesRegex(ValueError, "do not match"):
            validate_sr_guides(motion, other_video)

    def test_nodes_preserve_source_and_lazy_selection_and_manifest_fingerprint(self):
        from test_media_pipeline import node_modules
        value = self.manifest()
        provider = self.load(value)
        sequence = {"schema_version": 2, "video": self.video, "public": {"width": 2, "height": 2}}
        with node_modules():
            nodes = importlib.import_module("comfy_dlss_experimental.nodes.external_guides")
            cls = nodes.DLSSExperimentalExternalGuide
            fingerprint = cls.fingerprint_inputs(str(provider.manifest_path))
            result = cls.execute(sequence, str(provider.manifest_path)).result[0]
            self.assertIs(result.source_video, self.video)
            selector = nodes.DLSSExperimentalGuideSelector
            self.assertEqual(selector.check_lazy_status("b", a=result), ["b"])
            self.assertEqual(selector.check_lazy_status("b", b=result), [])
            self.assertTrue(all(item.lazy for item in selector.define_schema().inputs[1:]))
            self.assertIs(selector.execute("b", a=object(), b=result).result[0], result)
            value["frames"][1]["reset"] = True
            self.load(value)
            self.assertNotEqual(fingerprint, cls.fingerprint_inputs(str(provider.manifest_path)))

    @unittest.skipUnless(HAS_NUMPY, "NumPy optional media dependency not installed")
    def test_numeric_npy_and_nr_timestamp_adapter(self):
        import numpy as np
        value = self.manifest(storage="npy")
        buffer = io.BytesIO()
        np.save(buffer, np.full((2, 2, 2), .25, dtype="<f4"), allow_pickle=False)
        data = buffer.getvalue()
        value["frames"][0]["sha256"] = hashlib.sha256(data).hexdigest()
        provider = self.load(value)
        (self.root / "frame.raw").write_bytes(data)
        array, info = provider.read_at(1000, source_sha256="a" * 64)
        self.assertEqual(array.shape, (2, 2, 2))
        self.assertTrue(np.all(array == .25))
        self.assertTrue(info["reset"])
        with self.assertRaisesRegex(ValueError, "timestamp"):
            provider.read_at(1001, source_sha256="a" * 64)
        for invalid in (np.ones((1, 2, 2), dtype="<f4"), np.ones((2, 2, 2), dtype="uint8"),
                        np.ones((2, 2, 2), dtype="object")):
            buffer = io.BytesIO()
            np.save(buffer, invalid)
            data = buffer.getvalue()
            value["frames"][0]["sha256"] = hashlib.sha256(data).hexdigest()
            provider = self.load(value)
            (self.root / "frame.raw").write_bytes(data)
            with self.assertRaises(ValueError): self.frame(provider)

    @unittest.skipUnless(HAS_NUMPY, "NumPy optional media dependency not installed")
    def test_nr_rejects_ambiguous_time_and_half_overflow(self):
        value = self.manifest(values=[70000.] * 8)
        provider = self.load(value)
        with self.assertRaisesRegex(ValueError, "FP16"):
            provider.read_at(0, source_sha256="a" * 64)
        value = self.manifest()
        value["frames"][1]["pts_ns"] = 1500
        provider = self.load(value)
        with self.assertRaisesRegex(ValueError, "unique"):
            provider.read_at(750, source_sha256="a" * 64)


if __name__ == "__main__":
    unittest.main()
