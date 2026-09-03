from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Any, BinaryIO, Mapping, Sequence


MAGIC = b"CDLSSP1\0"
PROTOCOL_VERSION = 1
MAX_PACKET_BYTES = 512 * 1024 * 1024
MAX_PLANES = 8
MAX_DIMENSION = 16_384

_PACKET_HEADER = struct.Struct("<8sHHIQQ")
_FRAME_HEADER = struct.Struct("<IIIIqQII")
_PLANE_HEADER = struct.Struct("<HHIIIIIQ")


class ProtocolError(ValueError):
    """The peer supplied a malformed or unsupported sidecar packet."""


class MessageType(IntEnum):
    HELLO = 1
    CONFIGURE = 2
    CONFIGURED = 3
    FRAME = 4
    FRAME_RESULT = 5
    RESET = 6
    CANCEL = 7
    SHUTDOWN = 8
    ERROR = 9


class PacketFlags(IntFlag):
    NONE = 0
    RESET_HISTORY = 1 << 0
    END_OF_STREAM = 1 << 1


class PlaneSemantic(IntEnum):
    COLOR = 1
    MOTION = 2
    DEPTH = 3
    EXPOSURE = 4
    REACTIVE_MASK = 5
    OUTPUT_COLOR = 100


class PixelFormat(IntEnum):
    RGBA8_UNORM = 1
    RGBA16_FLOAT = 2
    RG16_FLOAT = 3
    R32_FLOAT = 4
    R8_UNORM = 5

    @property
    def bytes_per_pixel(self) -> int:
        return {
            PixelFormat.RGBA8_UNORM: 4,
            PixelFormat.RGBA16_FLOAT: 8,
            PixelFormat.RG16_FLOAT: 4,
            PixelFormat.R32_FLOAT: 4,
            PixelFormat.R8_UNORM: 1,
        }[self]


@dataclass(frozen=True, slots=True)
class Packet:
    message_type: MessageType
    request_id: int
    flags: PacketFlags
    payload: bytes


@dataclass(frozen=True, slots=True)
class FramePlane:
    semantic: PlaneSemantic
    pixel_format: PixelFormat
    width: int
    height: int
    row_pitch: int
    data: bytes
    flags: int = 0

    def validate(self) -> None:
        _validate_dimensions(self.width, self.height)
        if not isinstance(self.semantic, PlaneSemantic):
            raise ProtocolError(f"unsupported plane semantic: {self.semantic!r}")
        if not isinstance(self.pixel_format, PixelFormat):
            raise ProtocolError(f"unsupported pixel format: {self.pixel_format!r}")
        minimum_pitch = self.width * self.pixel_format.bytes_per_pixel
        if self.row_pitch < minimum_pitch or self.row_pitch > (1 << 32) - 1:
            raise ProtocolError(
                f"{self.semantic.name} row pitch {self.row_pitch} is outside [{minimum_pitch}, 2^32-1]"
            )
        expected_size = self.row_pitch * self.height
        if len(self.data) != expected_size:
            raise ProtocolError(
                f"{self.semantic.name} contains {len(self.data)} bytes; expected {expected_size}"
            )
        if self.flags != 0:
            raise ProtocolError(f"unsupported plane flags: 0x{self.flags:x}")


@dataclass(frozen=True, slots=True)
class FramePayload:
    input_width: int
    input_height: int
    output_width: int
    output_height: int
    pts_ns: int
    frame_index: int
    planes: tuple[FramePlane, ...]

    def validate(self) -> None:
        _validate_dimensions(self.input_width, self.input_height)
        _validate_dimensions(self.output_width, self.output_height)
        if self.pts_ns < -(1 << 63) or self.pts_ns > (1 << 63) - 1:
            raise ProtocolError("pts_ns is outside int64 range")
        if self.frame_index < 0 or self.frame_index > (1 << 64) - 1:
            raise ProtocolError("frame_index is outside uint64 range")
        if not 1 <= len(self.planes) <= MAX_PLANES:
            raise ProtocolError(f"a frame must contain between 1 and {MAX_PLANES} planes")
        semantics: set[PlaneSemantic] = set()
        for plane in self.planes:
            plane.validate()
            if plane.semantic in semantics:
                raise ProtocolError(f"duplicate plane semantic: {plane.semantic.name}")
            semantics.add(plane.semantic)


def _validate_dimensions(width: int, height: int) -> None:
    if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION:
        raise ProtocolError(
            f"dimensions must be between 1 and {MAX_DIMENSION}; received {width}x{height}"
        )


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise EOFError(f"stream ended after {len(chunks)} of {size} bytes")
        chunks.extend(chunk)
    return bytes(chunks)


def encode_packet(packet: Packet) -> bytes:
    if not 0 <= packet.request_id <= (1 << 64) - 1:
        raise ProtocolError("request_id is outside uint64 range")
    if len(packet.payload) > MAX_PACKET_BYTES:
        raise ProtocolError(f"payload exceeds the {MAX_PACKET_BYTES}-byte limit")
    known_flags = PacketFlags.RESET_HISTORY | PacketFlags.END_OF_STREAM
    unknown_flags = int(packet.flags) & ~int(known_flags)
    if unknown_flags:
        raise ProtocolError(f"unsupported packet flags: 0x{int(packet.flags):x}")
    header = _PACKET_HEADER.pack(
        MAGIC,
        PROTOCOL_VERSION,
        int(packet.message_type),
        int(packet.flags),
        packet.request_id,
        len(packet.payload),
    )
    return header + packet.payload


def read_packet(stream: BinaryIO, *, max_payload_bytes: int = MAX_PACKET_BYTES) -> Packet:
    if not 0 <= max_payload_bytes <= MAX_PACKET_BYTES:
        raise ValueError(f"max_payload_bytes must be between 0 and {MAX_PACKET_BYTES}")
    raw_header = _read_exact(stream, _PACKET_HEADER.size)
    magic, version, raw_type, raw_flags, request_id, payload_size = _PACKET_HEADER.unpack(raw_header)
    if magic != MAGIC:
        raise ProtocolError("invalid sidecar packet magic")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported sidecar protocol version: {version}")
    try:
        message_type = MessageType(raw_type)
    except ValueError as exc:
        raise ProtocolError(f"unsupported message type: {raw_type}") from exc
    known_flags = int(PacketFlags.RESET_HISTORY | PacketFlags.END_OF_STREAM)
    if raw_flags & ~known_flags:
        raise ProtocolError(f"unsupported packet flags: 0x{raw_flags:x}")
    if payload_size > max_payload_bytes:
        raise ProtocolError(f"payload size {payload_size} exceeds limit {max_payload_bytes}")
    return Packet(message_type, request_id, PacketFlags(raw_flags), _read_exact(stream, payload_size))


def encode_json_payload(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_json_payload(payload: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid UTF-8 JSON payload: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ProtocolError("JSON payload root must be an object")
    return decoded


def encode_frame_payload(frame: FramePayload) -> bytes:
    frame.validate()
    payload_size = _FRAME_HEADER.size + len(frame.planes) * _PLANE_HEADER.size + sum(
        len(plane.data) for plane in frame.planes
    )
    if payload_size > MAX_PACKET_BYTES:
        raise ProtocolError(f"encoded frame exceeds the {MAX_PACKET_BYTES}-byte packet limit")
    descriptors = bytearray()
    data = bytearray()
    for plane in frame.planes:
        descriptors.extend(
            _PLANE_HEADER.pack(
                int(plane.semantic),
                int(plane.pixel_format),
                plane.width,
                plane.height,
                plane.row_pitch,
                plane.flags,
                0,
                len(plane.data),
            )
        )
        data.extend(plane.data)
    return (
        _FRAME_HEADER.pack(
            frame.input_width,
            frame.input_height,
            frame.output_width,
            frame.output_height,
            frame.pts_ns,
            frame.frame_index,
            len(frame.planes),
            0,
        )
        + descriptors
        + data
    )


def decode_frame_payload(payload: bytes) -> FramePayload:
    if len(payload) > MAX_PACKET_BYTES:
        raise ProtocolError("frame payload exceeds the packet limit")
    if len(payload) < _FRAME_HEADER.size:
        raise ProtocolError("frame payload is shorter than its header")
    (
        input_width,
        input_height,
        output_width,
        output_height,
        pts_ns,
        frame_index,
        plane_count,
        reserved,
    ) = _FRAME_HEADER.unpack_from(payload)
    _validate_dimensions(input_width, input_height)
    _validate_dimensions(output_width, output_height)
    if reserved != 0:
        raise ProtocolError("frame header reserved field must be zero")
    if not 1 <= plane_count <= MAX_PLANES:
        raise ProtocolError(f"invalid frame plane count: {plane_count}")
    descriptor_end = _FRAME_HEADER.size + plane_count * _PLANE_HEADER.size
    if descriptor_end > len(payload):
        raise ProtocolError("frame payload is shorter than its plane descriptor table")

    descriptors: list[tuple[PlaneSemantic, PixelFormat, int, int, int, int, int]] = []
    semantics: set[PlaneSemantic] = set()
    data_size = 0
    for index in range(plane_count):
        offset = _FRAME_HEADER.size + index * _PLANE_HEADER.size
        raw_semantic, raw_format, width, height, row_pitch, flags, reserved, byte_count = (
            _PLANE_HEADER.unpack_from(payload, offset)
        )
        if reserved != 0:
            raise ProtocolError(f"plane descriptor reserved field must be zero at index {index}")
        try:
            semantic = PlaneSemantic(raw_semantic)
            pixel_format = PixelFormat(raw_format)
        except ValueError as exc:
            raise ProtocolError(f"unknown plane semantic or pixel format at index {index}") from exc
        _validate_dimensions(width, height)
        if semantic in semantics:
            raise ProtocolError(f"duplicate plane semantic: {semantic.name}")
        semantics.add(semantic)
        if row_pitch < width * pixel_format.bytes_per_pixel or byte_count != row_pitch * height:
            raise ProtocolError(f"invalid plane row pitch or byte count at index {index}")
        if flags:
            raise ProtocolError(f"unsupported plane flags: 0x{flags:x}")
        if byte_count > MAX_PACKET_BYTES - descriptor_end - data_size:
            raise ProtocolError("frame plane sizes overflow the packet limit")
        data_size += byte_count
        descriptors.append((semantic, pixel_format, width, height, row_pitch, flags, byte_count))
    if descriptor_end + data_size != len(payload):
        raise ProtocolError("frame plane byte counts do not match the payload size")

    planes: list[FramePlane] = []
    data_offset = descriptor_end
    for semantic, pixel_format, width, height, row_pitch, flags, byte_count in descriptors:
        next_offset = data_offset + byte_count
        planes.append(
            FramePlane(
                semantic=semantic,
                pixel_format=pixel_format,
                width=width,
                height=height,
                row_pitch=row_pitch,
                flags=flags,
                data=payload[data_offset:next_offset],
            )
        )
        data_offset = next_offset
    frame = FramePayload(
        input_width=input_width,
        input_height=input_height,
        output_width=output_width,
        output_height=output_height,
        pts_ns=pts_ns,
        frame_index=frame_index,
        planes=tuple(planes),
    )
    frame.validate()
    return frame


def make_frame(
    *,
    input_width: int,
    input_height: int,
    output_width: int,
    output_height: int,
    pts_ns: int,
    frame_index: int,
    planes: Sequence[FramePlane],
) -> FramePayload:
    return FramePayload(
        input_width,
        input_height,
        output_width,
        output_height,
        pts_ns,
        frame_index,
        tuple(planes),
    )
