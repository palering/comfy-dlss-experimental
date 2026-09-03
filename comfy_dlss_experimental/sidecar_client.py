from __future__ import annotations

from typing import Any, BinaryIO, Mapping

from .sidecar_protocol import (
    FramePayload,
    MessageType,
    Packet,
    PacketFlags,
    PlaneSemantic,
    ProtocolError,
    decode_frame_payload,
    decode_json_payload,
    encode_frame_payload,
    encode_json_payload,
    encode_packet,
    read_packet,
)


class SidecarRemoteError(RuntimeError):
    pass


class SidecarClient:
    """Single-threaded request/response client for a blocking pipe or TCP stream."""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self._reader = reader
        self._writer = writer
        self._next_request_id = 1
        self._closed = False

    def _exchange(
        self,
        message_type: MessageType,
        expected_type: MessageType,
        payload: bytes = b"",
        *,
        flags: PacketFlags = PacketFlags.NONE,
    ) -> Packet:
        if self._closed:
            raise RuntimeError("sidecar client is closed")
        request_id = self._next_request_id
        self._next_request_id += 1
        encoded = memoryview(encode_packet(Packet(message_type, request_id, flags, payload)))
        try:
            while encoded:
                written = self._writer.write(encoded)
                if written is None or written <= 0 or written > len(encoded):
                    raise OSError("sidecar stream failed to make write progress")
                encoded = encoded[written:]
            self._writer.flush()
            response = read_packet(self._reader)
        except (OSError, EOFError, ProtocolError):
            # A partial packet cannot be retried on the same byte stream.
            self._closed = True
            raise
        if response.request_id != request_id:
            self._closed = True
            raise ProtocolError(
                f"sidecar response request_id {response.request_id} does not match {request_id}"
            )
        if response.message_type == MessageType.ERROR:
            self._closed = True
            details = decode_json_payload(response.payload)
            stage = str(details.get("stage", "sidecar"))
            message = str(details.get("error", "unknown sidecar error"))
            raise SidecarRemoteError(f"{stage}: {message}")
        if response.message_type != expected_type:
            self._closed = True
            raise ProtocolError(
                f"expected {expected_type.name}, received {response.message_type.name}"
            )
        return response

    def hello(self) -> dict[str, Any]:
        response = self._exchange(MessageType.HELLO, MessageType.HELLO)
        return decode_json_payload(response.payload)

    def configure(self, configuration: Mapping[str, Any]) -> dict[str, Any]:
        response = self._exchange(
            MessageType.CONFIGURE,
            MessageType.CONFIGURED,
            encode_json_payload(configuration),
        )
        return decode_json_payload(response.payload)

    def process_frame(self, frame: FramePayload, *, reset_history: bool = False) -> FramePayload:
        flags = PacketFlags.RESET_HISTORY if reset_history else PacketFlags.NONE
        response = self._exchange(
            MessageType.FRAME,
            MessageType.FRAME_RESULT,
            encode_frame_payload(frame),
            flags=flags,
        )
        try:
            result = decode_frame_payload(response.payload)
            if result.frame_index != frame.frame_index or result.pts_ns != frame.pts_ns:
                raise ProtocolError("sidecar frame result changed its frame index or timestamp")
            if (result.input_width, result.input_height, result.output_width, result.output_height) != (
                frame.input_width, frame.input_height, frame.output_width, frame.output_height
            ):
                raise ProtocolError("sidecar frame result changed its dimensions")
            color = next((plane for plane in result.planes if plane.semantic == PlaneSemantic.OUTPUT_COLOR), None)
            if color is None:
                raise ProtocolError("sidecar frame result has no OUTPUT_COLOR plane")
            if (color.width, color.height) != (frame.output_width, frame.output_height):
                raise ProtocolError("OUTPUT_COLOR dimensions do not match the requested output")
            return result
        except ProtocolError:
            self._closed = True
            raise

    def reset(self) -> dict[str, Any]:
        response = self._exchange(MessageType.RESET, MessageType.RESET)
        return decode_json_payload(response.payload)

    def shutdown(self) -> dict[str, Any]:
        response = self._exchange(MessageType.SHUTDOWN, MessageType.SHUTDOWN)
        self._closed = True
        return decode_json_payload(response.payload)
