from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from comfy_dlss_experimental.sl_bundle import load_bundle, MAX_MANIFEST_BYTES
from comfy_dlss_experimental.sl_contract import SLFrameMetadata
from tests.test_sl_contract import camera, IDENTITY


def make_bundle(root, *, rr=False, count=4):
    root.mkdir(parents=True, exist_ok=True)
    data = {"color": struct.pack("<4e", .25, .5, .75, 1) * 4096,
            "motion": bytes(4096 * 4), "depth": struct.pack("<f", .5) * 4096}
    if rr:
        data.update(diffuse_albedo=data["color"], specular_albedo=data["color"],
                    normal_roughness=struct.pack("<4e", 0, 0, -1, .5) * 4096,
                    specular_motion=data["motion"])
    planes = {}
    for role, raw in data.items():
        (root / (role + ".bin")).write_bytes(raw)
        planes[role] = {"path": role + ".bin", "sha256": hashlib.sha256(raw).hexdigest()}
    frames = [{"pts_ns": round(i * 1_000_000_000 / 30), "camera": asdict(camera()),
               "metadata": asdict(SLFrameMetadata(reset=i == 2)), "planes": deepcopy(planes),
               **({"world_to_view": IDENTITY, "view_to_world": IDENTITY} if rr else {})} for i in range(count)]
    value = {"schema_version": 1, "kind": "dlss_reconstruction_bundle", "width": 64, "height": 64,
             "fps": "30/1", "color_transfer": "linear_sdr" if rr else "srgb",
             "jitter_policy": "unjittered_video", "depth_inverted": False, "has_rr": rr,
             "frames": frames, "audio_source": None}
    path = root / "bundle.json"
    path.write_text(json.dumps(value))
    return path, value


class BundleTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "tmp" / "sl-bundle-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path, self.value = make_bundle(self.root / "bundle")

    def write(self, value):
        self.path.write_text(json.dumps(value))

    def test_load_reopen_and_exact_planes(self):
        bundle = load_bundle(self.path)
        self.assertEqual(bundle, bundle.reopen())
        self.assertEqual(bundle.fps, "30")
        color, motion, depth, frame, rr = bundle.read_frame(1, bundle.settings(mode="dlaa"))
        self.assertEqual((len(color), len(motion), len(depth)), (32768, 16384, 16384))
        self.assertEqual(frame.pts_ns, 33333333)
        self.assertIsNone(rr)
        self.assertFalse(bundle.report()["camera_inferred"])

    def test_rr_requires_all_planes_and_matrices(self):
        path, value = make_bundle(self.root / "rr", rr=True)
        bundle = load_bundle(path)
        settings = bundle.settings("rr", "dlaa")
        self.assertEqual(settings.color_transfer, "linear_sdr")
        rr = bundle.read_frame(0, settings)[4]
        self.assertEqual(len(rr.encode(settings)), 128 + 4096 * 28)
        with self.assertRaisesRegex(ValueError, "real albedos"):
            load_bundle(self.path).settings("rr", "dlaa")
        del value["frames"][0]["world_to_view"]
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "complete camera"):
            load_bundle(path)

    def test_manifest_and_payload_changes_are_not_silently_reused(self):
        bundle = load_bundle(self.path)
        payload = self.path.parent / "color.bin"
        data = bytearray(payload.read_bytes())
        data[0] ^= 1
        payload.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            bundle.read_frame(0, bundle.settings(mode="dlaa"))
        self.value["frames"][0]["metadata"]["reset"] = True
        self.write(self.value)
        with self.assertRaisesRegex(ValueError, "changed after"):
            bundle.reopen()

    def test_complete_metadata_and_strict_cfr(self):
        for change in (lambda v: v["frames"][0].pop("camera"),
                       lambda v: v["frames"][0]["metadata"].pop("jitter_x"),
                       lambda v: v["frames"][1].update(pts_ns=33340000),
                       lambda v: v["frames"][0].update(pts_ns=True),
                       lambda v: v.update(fps="nan"), lambda v: v.update(schema_version=True),
                       lambda v: v["frames"][0]["camera"].update(aspect=2),
                       lambda v: v["frames"][0]["metadata"].update(jitter_x=.25)):
            value = deepcopy(self.value)
            change(value)
            self.write(value)
            with self.assertRaises(ValueError):
                load_bundle(self.path)

    def test_path_escape_and_symlink_are_rejected(self):
        outside = self.root / "outside.bin"
        outside.write_bytes((self.path.parent / "color.bin").read_bytes())
        (self.path.parent / "escape.bin").symlink_to(outside)
        for raw in ("../outside.bin", str(outside), "escape.bin", "C:\\file.bin"):
            value = deepcopy(self.value)
            value["frames"][0]["planes"]["color"]["path"] = raw
            self.write(value)
            with self.assertRaises(ValueError):
                load_bundle(self.path)

    def test_duplicate_json_size_and_cancel(self):
        self.path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_bundle(self.path)
        with self.path.open("wb") as file:
            file.truncate(MAX_MANIFEST_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "32 MiB"):
            load_bundle(self.path)
        with self.assertRaises(InterruptedError):
            load_bundle(self.path, cancelled=lambda: True)

    def test_settings_cannot_reinterpret_transfer_or_inverted_depth(self):
        bundle = load_bundle(self.path)
        for change in ({"color_transfer": "linear_sdr"}, {"depth_inverted": True}):
            with self.assertRaisesRegex(ValueError, "declarations"):
                bundle.read_frame(0, replace(bundle.settings(mode="dlaa"), **change))
