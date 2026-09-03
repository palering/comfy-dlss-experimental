from __future__ import annotations

import io
import unittest
from dataclasses import replace

from comfy_dlss_experimental.sidecar_client import SidecarClient, SidecarRemoteError
from comfy_dlss_experimental.sidecar_protocol import (
    FramePayload,
    FramePlane,
    MessageType,
    Packet,
    PacketFlags,
    PixelFormat,
    PlaneSemantic,
    ProtocolError,
    encode_frame_payload,
    encode_json_payload,
    encode_packet,
    read_packet,
)


class SidecarClientTests(unittest.TestCase):
    def test_short_writes_are_completed(self) -> None:
        class ShortWriter(io.BytesIO):
            def write(self, data):
                return super().write(data[:3])
        writer = ShortWriter()
        response = encode_packet(Packet(MessageType.HELLO, 1, PacketFlags.NONE, b"{}"))
        SidecarClient(io.BytesIO(response), writer).hello()
        expected = encode_packet(Packet(MessageType.HELLO, 1, PacketFlags.NONE, b""))
        self.assertEqual(writer.getvalue(), expected)

    def test_failed_write_closes_client(self) -> None:
        class FailedWriter(io.BytesIO):
            def write(self, data):
                return 0
        client = SidecarClient(io.BytesIO(), FailedWriter())
        with self.assertRaises(OSError):
            client.hello()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            client.hello()

    def test_wrong_output_dimensions_rejected(self) -> None:
        frame = FramePayload(2, 2, 2, 2, 0, 0, (
            FramePlane(PlaneSemantic.COLOR, PixelFormat.RGBA8_UNORM, 2, 2, 8, bytes(16)),
        ))
        for output in (
            replace(frame, output_width=1, planes=(
                FramePlane(PlaneSemantic.OUTPUT_COLOR, PixelFormat.RGBA8_UNORM, 1, 2, 4, bytes(8)),
            )),
            replace(frame, planes=(
                FramePlane(PlaneSemantic.OUTPUT_COLOR, PixelFormat.RGBA8_UNORM, 1, 2, 4, bytes(8)),
            )),
        ):
            response = encode_packet(Packet(MessageType.FRAME_RESULT, 1, PacketFlags.NONE, encode_frame_payload(output)))
            with self.assertRaisesRegex(ProtocolError, "dimensions"):
                SidecarClient(io.BytesIO(response), io.BytesIO()).process_frame(frame)

    def test_session_round_trip_and_written_requests(self) -> None:
        source = bytes(range(16))
        result_frame = FramePayload(
            2,
            2,
            2,
            2,
            10,
            7,
            (FramePlane(PlaneSemantic.OUTPUT_COLOR, PixelFormat.RGBA8_UNORM, 2, 2, 8, source),),
        )
        responses = b"".join(
            (
                encode_packet(
                    Packet(
                        MessageType.HELLO,
                        1,
                        PacketFlags.NONE,
                        encode_json_payload({"renderer": "test"}),
                    )
                ),
                encode_packet(
                    Packet(
                        MessageType.CONFIGURED,
                        2,
                        PacketFlags.NONE,
                        encode_json_payload({"accepted": True}),
                    )
                ),
                encode_packet(
                    Packet(
                        MessageType.FRAME_RESULT,
                        3,
                        PacketFlags.RESET_HISTORY,
                        encode_frame_payload(result_frame),
                    )
                ),
                encode_packet(
                    Packet(
                        MessageType.SHUTDOWN,
                        4,
                        PacketFlags.NONE,
                        encode_json_payload({"shutdown": True}),
                    )
                ),
            )
        )
        reader = io.BytesIO(responses)
        writer = io.BytesIO()
        client = SidecarClient(reader, writer)
        self.assertEqual(client.hello()["renderer"], "test")
        self.assertTrue(client.configure({"mode": "passthrough"})["accepted"])
        input_frame = FramePayload(
            2,
            2,
            2,
            2,
            10,
            7,
            (FramePlane(PlaneSemantic.COLOR, PixelFormat.RGBA8_UNORM, 2, 2, 8, source),),
        )
        self.assertEqual(client.process_frame(input_frame, reset_history=True), result_frame)
        self.assertTrue(client.shutdown()["shutdown"])

        requests = io.BytesIO(writer.getvalue())
        self.assertEqual(read_packet(requests).message_type, MessageType.HELLO)
        self.assertEqual(read_packet(requests).message_type, MessageType.CONFIGURE)
        frame_request = read_packet(requests)
        self.assertEqual(frame_request.message_type, MessageType.FRAME)
        self.assertEqual(frame_request.flags, PacketFlags.RESET_HISTORY)
        self.assertEqual(read_packet(requests).message_type, MessageType.SHUTDOWN)

    def test_remote_error_is_raised(self) -> None:
        response = encode_packet(
            Packet(
                MessageType.ERROR,
                1,
                PacketFlags.NONE,
                encode_json_payload({"stage": "frame", "error": "bad texture"}),
            )
        )
        client = SidecarClient(io.BytesIO(response), io.BytesIO())
        with self.assertRaisesRegex(SidecarRemoteError, "frame: bad texture"):
            client.hello()

    def test_mismatched_request_id_is_rejected(self) -> None:
        response = encode_packet(Packet(MessageType.HELLO, 9, PacketFlags.NONE, b"{}"))
        client = SidecarClient(io.BytesIO(response), io.BytesIO())
        with self.assertRaisesRegex(ProtocolError, "does not match"):
            client.hello()
