"""Deterministic raw-plane fixtures; no model, decoder, or NumPy dependency."""
from __future__ import annotations

import struct
from dataclasses import replace
from typing import Iterator

from comfy_dlss_experimental.sidecar_protocol import FramePayload, FramePlane, PixelFormat, PlaneSemantic


def active_bytes(plane: FramePlane) -> bytes:
    pitch = plane.width * plane.pixel_format.bytes_per_pixel
    return b"".join(
        plane.data[row * plane.row_pitch:row * plane.row_pitch + pitch]
        for row in range(plane.height)
    )


def fixture(width: int, height: int, *, fp16: bool, guides: bool, padding: int) -> FramePayload:
    specifications = [(PlaneSemantic.COLOR, PixelFormat.RGBA16_FLOAT if fp16 else PixelFormat.RGBA8_UNORM)]
    if guides:
        specifications += [
            (PlaneSemantic.MOTION, PixelFormat.RG16_FLOAT),
            (PlaneSemantic.DEPTH, PixelFormat.R32_FLOAT),
            (PlaneSemantic.REACTIVE_MASK, PixelFormat.R8_UNORM),
            (PlaneSemantic.EXPOSURE, PixelFormat.R32_FLOAT),
        ]
    planes = []
    for semantic, pixel_format in specifications:
        w, h = (1, 1) if semantic == PlaneSemantic.EXPOSURE else (width, height)
        data = bytearray()
        for y in range(h):
            for x in range(w):
                if semantic == PlaneSemantic.COLOR:
                    if fp16:
                        data.extend(struct.pack("<4e", x % 7 / 2, y % 5 / 4, (x + y) % 3 / 4, 1))
                    else:
                        data.extend((255 if (x // 4 + y // 4) % 2 else 0, x * 17 % 256, y * 29 % 256, 255))
                elif semantic == PlaneSemantic.MOTION:
                    data.extend(struct.pack("<2e", (x % 9 - 4) / 8, (y % 7 - 3) / 4))
                elif semantic == PlaneSemantic.DEPTH:
                    data.extend(struct.pack("<f", 0.1 + (x + y) % 90 / 100))
                elif semantic == PlaneSemantic.EXPOSURE:
                    data.extend(struct.pack("<f", 1.25))
                else:
                    data.append((x + y) % 3 * 127)
            data.extend(b"\xa5" * padding)
        planes.append(FramePlane(semantic, pixel_format, w, h, w * pixel_format.bytes_per_pixel + padding, bytes(data)))
    return FramePayload(width, height, width, height, 0, 0, tuple(planes))


def smoke_frames(cycles: int = 3) -> Iterator[FramePayload]:
    cases = [
        fixture(16, 16, fp16=False, guides=False, padding=0),
        fixture(17, 9, fp16=False, guides=False, padding=3),
        fixture(17, 9, fp16=True, guides=True, padding=5),
        fixture(320, 181, fp16=True, guides=True, padding=7),
        fixture(1, 1, fp16=False, guides=False, padding=1),
    ]
    for index in range(cycles * len(cases)):
        yield replace(cases[index % len(cases)], frame_index=index, pts_ns=-2_000_000 + index * 33_366_700)


def verify_readback(source: FramePayload, returned: FramePayload) -> None:
    if (source.input_width, source.input_height, source.output_width, source.output_height,
        source.pts_ns, source.frame_index) != (
        returned.input_width, returned.input_height, returned.output_width, returned.output_height,
        returned.pts_ns, returned.frame_index
    ):
        raise AssertionError("frame metadata changed during GPU readback")
    expected = {
        PlaneSemantic.OUTPUT_COLOR if p.semantic == PlaneSemantic.COLOR else p.semantic: p
        for p in source.planes
    }
    if {p.semantic for p in returned.planes} != set(expected):
        raise AssertionError("GPU readback returned unexpected plane semantics")
    for actual in returned.planes:
        original = expected[actual.semantic]
        if (actual.width, actual.height, actual.pixel_format) != (
            original.width, original.height, original.pixel_format
        ) or active_bytes(actual) != active_bytes(original):
            raise AssertionError(f"GPU readback mismatch for {actual.semantic.name}")
