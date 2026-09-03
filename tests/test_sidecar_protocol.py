from __future__ import annotations

import io
import struct
import unittest

from comfy_dlss_experimental.sidecar_protocol import (
    MAGIC,
    FramePayload,
    FramePlane,
    MessageType,
    Packet,
    PacketFlags,
    PixelFormat,
    PlaneSemantic,
    ProtocolError,
    decode_frame_payload,
    decode_json_payload,
    encode_frame_payload,
    encode_json_payload,
    encode_packet,
    read_packet,
)


class SidecarPacketTests(unittest.TestCase):
    def test_packet_and_json_round_trip(self) -> None:
        payload = encode_json_payload({"mode": "dlaa", "frame_count": 3})
        encoded = encode_packet(Packet(MessageType.CONFIGURE, 42, PacketFlags.NONE, payload))
        decoded = read_packet(io.BytesIO(encoded))
        self.assertEqual(decoded.message_type, MessageType.CONFIGURE)
        self.assertEqual(decoded.request_id, 42)
        self.assertEqual(decode_json_payload(decoded.payload), {"mode": "dlaa", "frame_count": 3})

    def test_reader_rejects_advertised_payload_before_reading_it(self) -> None:
        header = struct.pack("<8sHHIQQ", MAGIC, 1, MessageType.FRAME, 0, 1, 4096)
        with self.assertRaisesRegex(ProtocolError, "exceeds limit"):
            read_packet(io.BytesIO(header), max_payload_bytes=1024)

    def test_reader_rejects_unknown_flags(self) -> None:
        header = struct.pack("<8sHHIQQ", MAGIC, 1, MessageType.RESET, 0x8000, 1, 0)
        with self.assertRaisesRegex(ProtocolError, "unsupported packet flags"):
            read_packet(io.BytesIO(header))

    def test_encoder_rejects_unknown_flags(self) -> None:
        packet = Packet(MessageType.RESET, 1, PacketFlags(0x8000), b"")
        with self.assertRaisesRegex(ProtocolError, "unsupported packet flags"):
            encode_packet(packet)


class SidecarFrameTests(unittest.TestCase):
    def test_frame_round_trip_with_temporal_guides(self) -> None:
        frame = FramePayload(
            input_width=2,
            input_height=2,
            output_width=4,
            output_height=4,
            pts_ns=33_366_700,
            frame_index=1,
            planes=(
                FramePlane(
                    PlaneSemantic.COLOR,
                    PixelFormat.RGBA8_UNORM,
                    2,
                    2,
                    8,
                    bytes(range(16)),
                ),
                FramePlane(
                    PlaneSemantic.MOTION,
                    PixelFormat.RG16_FLOAT,
                    2,
                    2,
                    8,
                    bytes(16),
                ),
                FramePlane(
                    PlaneSemantic.DEPTH,
                    PixelFormat.R32_FLOAT,
                    2,
                    2,
                    8,
                    bytes(16),
                ),
            ),
        )
        self.assertEqual(decode_frame_payload(encode_frame_payload(frame)), frame)

    def test_frame_allows_padded_rows(self) -> None:
        frame = FramePayload(
            2,
            1,
            2,
            1,
            0,
            0,
            (FramePlane(PlaneSemantic.COLOR, PixelFormat.RGBA8_UNORM, 2, 1, 16, bytes(16)),),
        )
        self.assertEqual(decode_frame_payload(encode_frame_payload(frame)), frame)

    def test_frame_rejects_short_plane_data(self) -> None:
        frame = FramePayload(
            2,
            2,
            2,
            2,
            0,
            0,
            (FramePlane(PlaneSemantic.COLOR, PixelFormat.RGBA8_UNORM, 2, 2, 8, bytes(15)),),
        )
        with self.assertRaisesRegex(ProtocolError, "contains 15 bytes"):
            encode_frame_payload(frame)

    def test_frame_rejects_duplicate_semantics(self) -> None:
        plane = FramePlane(PlaneSemantic.COLOR, PixelFormat.RGBA8_UNORM, 1, 1, 4, bytes(4))
        frame = FramePayload(1, 1, 1, 1, 0, 0, (plane, plane))
        with self.assertRaisesRegex(ProtocolError, "duplicate plane semantic"):
            encode_frame_payload(frame)

    def test_decoder_rejects_trailing_or_missing_plane_bytes(self) -> None:
        frame = FramePayload(
            1,
            1,
            1,
            1,
            0,
            0,
            (FramePlane(PlaneSemantic.COLOR, PixelFormat.RGBA8_UNORM, 1, 1, 4, bytes(4)),),
        )
        encoded = encode_frame_payload(frame)
        with self.assertRaisesRegex(ProtocolError, "do not match"):
            decode_frame_payload(encoded + b"x")
        with self.assertRaisesRegex(ProtocolError, "do not match"):
            decode_frame_payload(encoded[:-1])


if __name__ == "__main__":
    unittest.main()
