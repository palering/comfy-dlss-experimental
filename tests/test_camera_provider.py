from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

from comfy_dlss_experimental.camera_provider import load_camera_manifest, load_camera_for_sequence, validate_camera_depth
from comfy_dlss_experimental.camera_math import camera_from_opencv
from comfy_dlss_experimental.external_guides import load_external_guide
from comfy_dlss_experimental.sl_contract import CameraFrame, SLFrameMetadata
from tests.test_sl_contract import IDENTITY


def make_camera_inputs(root, source_sha256="a" * 64, count=5, video=None):
    root.mkdir(parents=True, exist_ok=True)
    # Explicit perspective projection; do not reuse the old wire-only identity
    # projection fixture as if it were calibrated device depth.
    q, r = 100 / 99.9, -10 / 99.9
    projection = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., q, 1., 0., 0., r, 0.)
    inverse = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 0., 1/r, 0., 0., 1., -q/r)
    camera = CameraFrame(projection, inverse, IDENTITY, IDENTITY, (0, 0, 0), (0, 1, 0), (1, 0, 0), (0, 0, 1), .1, 100, 1.57079632679, 1)
    source = {"sha256": source_sha256, "view_id": "source", "width": 64, "height": 64, "time_origin": "video_stream_start"}
    value = {"schema_version": 1, "kind": "dlss_camera_timeline", "source": source, "fps": "30", "depth_inverted": False,
             "provenance": "synthetic_test", "frames": [{"pts_ns": round(i * 1e9 / 30), "camera": asdict(camera),
             "metadata": asdict(SLFrameMetadata(reset=i in (0, 2)))} for i in range(count)]}
    path = root / "camera.json"
    path.write_text(json.dumps(value))
    data = struct.pack("<f", .5) * 4096
    (root / "depth.raw").write_bytes(data)
    depth_value = {"schema_version": 1, "source": source, "role": "depth", "semantics": "device_z", "units": "zero_to_one",
        "grid": {"width": 64, "height": 64, "sampling": "pixel_centers"}, "dtype": "float32_le", "storage": "raw",
        "metadata": {"reversed_z": False, "near": .1, "far": 100, "projection": "perspective"},
        "frames": [{"path": "depth.raw", "sha256": hashlib.sha256(data).hexdigest(), "pts_ns": f["pts_ns"]} for f in value["frames"]]}
    depth_path = root / "depth.json"
    depth_path.write_text(json.dumps(depth_value))
    return replace(load_camera_manifest(path), source_video=video), load_external_guide(depth_path, source_video=video), value


class CameraProviderTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / "tmp" / "camera-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.video = object()
        self.camera, self.depth, self.value = make_camera_inputs(self.root, video=self.video)

    def test_source_bound_reopen_timestamp_and_depth(self):
        validate_camera_depth(self.camera, self.depth)
        self.assertEqual(self.camera.index_at(33334333), 1)
        with self.assertRaisesRegex(ValueError, "unique"):
            self.camera.index_at(33334334)
        self.assertIs(self.camera.reopen().source_video, self.video)
        self.assertIs(deepcopy(self.camera), self.camera)
        from tests.test_media_pipeline import sequence
        seq = sequence()
        self.assertIs(load_camera_for_sequence(self.camera.manifest_path, seq).source_video, seq["video"])
        self.value["provenance"] = "estimated"
        self.camera.manifest_path.write_text(json.dumps(self.value))
        with self.assertRaisesRegex(ValueError, "changed"):
            self.camera.reopen()

    def test_malformed_or_inconsistent_metadata_is_rejected(self):
        changes = [lambda v: v.update(provenance=[]), lambda v: v.update(fps="nan"),
            lambda v: v.update(depth_inverted=True), lambda v: v["source"].update(view_id="crop"),
            lambda v: v["frames"][0]["metadata"].update(reset=False),
            lambda v: v["frames"][1]["metadata"].update(jitter_x=.1),
            lambda v: v["frames"][1]["metadata"].update(pre_exposure=2),
            lambda v: v["frames"][1].update(pts_ns=34000000),
            lambda v: v["frames"][0]["camera"].update(view_to_clip=IDENTITY, clip_to_view=IDENTITY)]
        for change in changes:
            value = deepcopy(self.value)
            change(value)
            self.camera.manifest_path.write_text(json.dumps(value))
            with self.subTest(change=change), self.assertRaises(ValueError):
                load_camera_manifest(self.camera.manifest_path)

    def test_camera_depth_projection_hash_and_grid_must_agree(self):
        for changes in ({"source_sha256": "b" * 64}, {"view_id": "crop"}, {"width": 32}, {"semantics": "linear_view_z"},
                        {"metadata_json": json.dumps({"reversed_z": True, "near": .1, "far": 100, "projection": "perspective"})},
                        {"metadata_json": json.dumps({"reversed_z": False, "near": .2, "far": 100, "projection": "perspective"})}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_camera_depth(self.camera, replace(self.depth, **changes))

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy optional")
    def test_opencv_conversion_pose_projection_and_temporal_reprojection(self):
        import numpy as np
        k = [[32., 0, 32], [0, 32., 32], [0, 0, 1]]
        previous = np.eye(4)
        current = np.eye(4)
        current[:3, 3] = [-.2, .1, 0]
        camera = camera_from_opencv(k, current, width=64, height=64, near=.1, far=100,
                                    previous_intrinsics=k, previous_world_to_camera=previous)
        self.assertTrue(np.allclose(camera.position, [.2, .1, 0]))
        world = np.array([.5, -.3, 2, 1])
        flip = np.diag([1., -1., 1., 1.])
        p = np.asarray(camera.view_to_clip).reshape(4, 4)
        clip = world @ (flip @ current @ flip).T @ p
        prior = clip @ np.asarray(camera.clip_to_previous).reshape(4, 4)
        self.assertTrue(np.allclose(prior, world @ p, atol=1e-6))
        for inverted in (False, True):
            converted = camera_from_opencv(k, current, width=64, height=64, near=.1, far=100, depth_inverted=inverted, reset=True)
            self.assertTrue(np.allclose(converted.clip_to_previous, IDENTITY, atol=1e-6))
        with self.assertRaisesRegex(ValueError, "previous"):
            camera_from_opencv(k, current, width=64, height=64, near=.1, far=100)
        k[0][2] = 30
        k[1][1] = 37
        off=camera_from_opencv(k,np.eye(4),width=64,height=64,near=.1,far=100,reset=True)
        xyz=np.array([.2,.3,2.])
        uv=np.asarray(k)@xyz;uv=uv[:2]/uv[2]
        clip=np.array([xyz[0],-xyz[1],xyz[2],1])@np.asarray(off.view_to_clip).reshape(4,4)
        screen=np.array([(clip[0]/clip[3]+1)*32,(1-clip[1]/clip[3])*32])
        self.assertTrue(np.allclose(screen,uv,atol=1e-5))
        k[0][1]=1
        with self.assertRaisesRegex(ValueError,"zero-skew"):
            camera_from_opencv(k,current,width=64,height=64,near=.1,far=100,reset=True)
