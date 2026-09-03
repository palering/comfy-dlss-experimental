from __future__ import annotations

import struct
import unittest
from dataclasses import replace
from unittest.mock import patch

from scripts.frame_smoke_cases import active_bytes, fixture, smoke_frames, verify_readback
from comfy_dlss_experimental.sidecar_protocol import (
    PlaneSemantic, ProtocolError, decode_frame_payload, encode_frame_payload,
)


class FrameFixtureTests(unittest.TestCase):
    def test_all_formats_and_padding_survive_wire_codec(self):
        frames = list(smoke_frames())
        self.assertEqual(len(frames), 15)
        self.assertEqual(sum(len(frame.planes) for frame in frames), 39)
        for frame in frames:
            self.assertEqual(decode_frame_payload(encode_frame_payload(frame)), frame)
            returned = replace(frame, planes=tuple(replace(
                plane,
                semantic=PlaneSemantic.OUTPUT_COLOR if plane.semantic == PlaneSemantic.COLOR else plane.semantic,
                row_pitch=plane.width * plane.pixel_format.bytes_per_pixel,
                data=active_bytes(plane),
            ) for plane in frame.planes))
            verify_readback(frame, returned)
            damaged = replace(returned, planes=tuple(replace(
                plane, data=bytes([plane.data[0] ^ 1]) + plane.data[1:]
            ) for plane in returned.planes))
            with self.assertRaisesRegex(AssertionError, "mismatch"):
                verify_readback(frame, damaged)

    def test_fp16_has_hdr_color_and_signed_motion(self):
        frame = fixture(17, 9, fp16=True, guides=True, padding=3)
        color = active_bytes(frame.planes[0])
        self.assertGreater(max(value for pixel in struct.iter_unpack("<4e", color) for value in pixel), 1)
        motion = active_bytes(frame.planes[1])
        self.assertLess(min(value for pixel in struct.iter_unpack("<2e", motion) for value in pixel), 0)
        self.assertGreater(max(value for pixel in struct.iter_unpack("<2e", motion) for value in pixel), 0)

    def test_packet_limit_checked_on_encode_and_decode(self):
        frame = fixture(17, 9, fp16=True, guides=True, padding=3)
        encoded = encode_frame_payload(frame)
        with patch("comfy_dlss_experimental.sidecar_protocol.MAX_PACKET_BYTES", 100):
            with self.assertRaisesRegex(ProtocolError, "packet limit"):
                encode_frame_payload(frame)
            with self.assertRaisesRegex(ProtocolError, "packet limit"):
                decode_frame_payload(encoded)
