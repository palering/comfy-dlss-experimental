from dataclasses import asdict, replace
import math
import struct
import unittest

from comfy_dlss_experimental.sl_contract import (
    CameraFrame, SLFrameMetadata, ReconstructionSettings, RRGuides, CAMERA_WIRE, FRAME_WIRE, SETTINGS_WIRE,
)

IDENTITY = tuple(float(i % 5 == 0) for i in range(16))


def camera(aspect=1):
    return CameraFrame(IDENTITY, IDENTITY, IDENTITY, IDENTITY, (0, 0, 0), (0, 1, 0),
                       (1, 0, 0), (0, 0, 1), .1, 100, math.pi / 2, aspect)


def config(rr=False):
    return ReconstructionSettings(64, 64, 64, 64, mode='dlaa', feature='rr' if rr else 'sr',
                                  color_transfer='linear_sdr' if rr else 'srgb')


def rr_guides():
    rgba = struct.pack('<4e', .5, .5, .5, 1) * 4096
    normal = struct.pack('<4e', 0, 0, -1, .5) * 4096
    return RRGuides(rgba, rgba, normal, bytes(4096 * 4), IDENTITY, IDENTITY)


class ReconstructionContractTests(unittest.TestCase):
    def test_camera_roundtrip_is_explicit_finite_and_frozen(self):
        value = camera()
        self.assertEqual(len(value.encode()), 324)
        self.assertEqual(CAMERA_WIRE.unpack(value.encode())[-5:], (value.near_plane, 100, value.vertical_fov, 1, 0))
        self.assertEqual(CameraFrame.from_payload(asdict(value)), value)
        payload = asdict(value)
        payload['position'] = [1, 2, 3]
        frozen = CameraFrame.from_payload(payload)
        payload['position'][0] = 999
        self.assertEqual(frozen.position, (1, 2, 3))
        for payload in ({}, None, {**asdict(value), 'guessed': True}):
            with self.assertRaises(ValueError):
                CameraFrame.from_payload(payload)

    def test_invalid_camera_binary32_inverse_axes_projection(self):
        cases = [('position', (True, 0, 0)), ('up', (1, 0, 0)), ('forward', (0, 0, 2)),
                 ('view_to_clip', (0,) * 16), ('position', (10**999, 0, 0)),
                 ('aspect', float('nan')), ('aspect', 1e100), ('near_plane', 1e-100),
                 ('far_plane', .01), ('vertical_fov', 4), ('orthographic', 1)]
        for field, value in cases:
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(camera(), **{field: value})
        for extent in ((2, 1), (64, 0), (True, 1)):
            with self.assertRaises(ValueError):
                camera().validate_extent(*extent)

    def test_frame_wire_sizes_and_settings_features(self):
        self.assertEqual(FRAME_WIRE.size, 48)
        self.assertEqual(SETTINGS_WIRE.unpack(config().encode()), (64, 64, 64, 64, 5, 0, 8, 0, 1, 0))
        self.assertEqual(SETTINGS_WIRE.unpack(config(True).encode()), (64, 64, 64, 64, 5, 0, 0, 0, 2, 0))
        self.assertEqual(config().frame_bytes, 48 + 324 + 4096 * 16)
        self.assertEqual(config(True).frame_bytes, 48 + 324 + 128 + 4096 * 44)
        self.assertLess(ReconstructionSettings(1920, 1080, 3840, 2160, feature='rr', color_transfer='linear_sdr').frame_bytes, 128*1024*1024)
        for field, value in [('feature', 'fg'), ('color_transfer', 'linear_hdr'), ('depth_inverted', 1),
                             ('input_width', True), ('output_width', 128)]:
            with self.assertRaises(ValueError):
                replace(config(), **{field: value})
        for field, value in [('preset', 'k'), ('color_transfer', 'srgb')]:
            with self.assertRaises(ValueError):
                replace(config(True), **{field: value})

    def test_metadata_underflow_and_policy(self):
        for field, value in [('jitter_x', .5001), ('motion_scale_x', 0), ('motion_scale_y', 1e-100),
                             ('exposure', 1e-100), ('pre_exposure', 65505), ('reset', 1)]:
            with self.assertRaises(ValueError):
                SLFrameMetadata(**{field: value})
        with self.assertRaises(ValueError):
            SLFrameMetadata(jitter_x=.25).validate_for(config())
        with self.assertRaises(ValueError):
            SLFrameMetadata(exposure=2).validate_for(config())
        SLFrameMetadata(exposure=2).validate_for(config(True))
        SLFrameMetadata(jitter_x=.25).validate_for(replace(config(), jitter_policy='external_render_metadata'))

    def test_rr_full_resolution_layout_and_matrix_validation(self):
        data = rr_guides().encode(config(True))
        self.assertEqual(len(data), 128 + 4096 * 28)
        self.assertEqual(struct.unpack_from('<32f', data), IDENTITY * 2)
        for name, value in [('diffuse_albedo', b''), ('normal_roughness', bytearray(4096 * 8))]:
            with self.assertRaises(ValueError):
                replace(rr_guides(), **{name: value}).encode(config(True))
        with self.assertRaises(ValueError):
            replace(rr_guides(), world_to_view=(0,) * 16)
