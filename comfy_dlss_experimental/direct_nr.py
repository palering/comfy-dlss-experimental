"""Strict adapter for the external D5V2 worker, not an NGX implementation.

The supported payload is RGBA8 color + current-to-previous RG16_FLOAT motion,
both tightly packed at the same dimensions. No depth, HDR or scaling is implied.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

VIDEO_HEADER = struct.Struct("<10I4f")
FRAME_HEADER = struct.Struct("<4Iq")
RESULT_HEADER = struct.Struct("<5Iq")
VIDEO_MAGIC = 0x32563544
FRAME_MAGIC = 0x314D5246
OUT_MAGIC = 0x3154554F


@dataclass(frozen=True)
class DirectNRSettings:
    width: int
    height: int
    frame_count: int
    warmup: int = 120
    profile: int = 1
    preset: int = 0
    style: int = 1
    auto_mask: int = 0
    ui_correction: int = 0
    intensity: float = 1.0
    local_tone: float = 1.0
    local_structure: float = 1.0
    skin_structure: float = -1.0

    def validate(self) -> None:
        for name, minimum, maximum in (
            ("width", 64, 7680), ("height", 64, 4320), ("frame_count", 1, 1_000_000),
            ("warmup", 1, 240), ("profile", 0, 3), ("preset", 0, 3), ("style", 0, 3),
            ("auto_mask", 0, 1), ("ui_correction", 0, 1),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
        for name in ("intensity", "local_tone", "local_structure", "skin_structure"):
            value = getattr(self, name)
            minimum = -1.0 if name == "skin_structure" else 0.0
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= 3:
                raise ValueError(f"{name} must be finite and in [{minimum}, 3]")

    @property
    def plane_bytes(self) -> int:
        self.validate()
        return self.width * self.height * 4

    def encode(self) -> bytes:
        self.validate()
        return VIDEO_HEADER.pack(
            VIDEO_MAGIC, self.width, self.height, self.warmup, self.frame_count,
            self.profile, self.preset, self.style, self.auto_mask, self.ui_correction,
            self.intensity, self.local_tone, self.local_structure, self.skin_structure,
        )


class DirectNRClient:
    def __init__(self, stream, settings: DirectNRSettings):
        settings.validate()
        self.stream = stream
        self.settings = settings
        self.index = 0
        self.failed = False
        stream.write(settings.encode())

    def process(self, color: bytes, motion: bytes, pts_ns: int, *, reset: bool = False) -> bytes:
        if self.failed or self.index >= self.settings.frame_count:
            raise RuntimeError("direct NR stream is failed or complete")
        if len(color) != self.settings.plane_bytes or len(motion) != self.settings.plane_bytes:
            raise ValueError("color and motion must be tightly packed 4 bytes per pixel")
        if type(pts_ns) is not int or not -(1 << 63) <= pts_ns < (1 << 63):
            raise ValueError("timestamp must be a signed 64-bit integer")
        try:
            self.stream.write(FRAME_HEADER.pack(FRAME_MAGIC, self.index, int(reset or self.index == 0), 0, pts_ns))
            self.stream.write(color)
            self.stream.write(motion)
            raw = self.stream.read_exact(RESULT_HEADER.size)
            magic, index, ok, count, result, pts = RESULT_HEADER.unpack(raw)
            if magic != OUT_MAGIC or index != self.index or pts != pts_ns or count != self.settings.plane_bytes:
                raise RuntimeError("external worker returned an invalid frame header")
            if ok != 1 or result != 1:
                raise RuntimeError(f"external worker Evaluate failed: 0x{result:08X}")
            output = self.stream.read_exact(count)
            self.index += 1
            return output
        except BaseException:
            self.failed = True
            raise

    def finish(self) -> dict:
        if self.failed or self.index != self.settings.frame_count:
            raise RuntimeError("cannot finish an incomplete or failed direct NR stream")
        self.stream.close_input()
        result = self.stream.wait()
        if result["exit_code"] != 0 or result["reason"] != 0:
            raise RuntimeError(f"external worker did not exit cleanly: {result}")
        return result
