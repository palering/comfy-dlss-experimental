"""Explicit SDR transfer conversion for the current full-range RGBA8 worker.

BT.709 primaries/D65 are shared with sRGB. This is transfer re-encoding, not
HDR tone mapping, display calibration or a claim about NR's preferred input.
BT.709 uses the nominal inverse OETF, not an assumed BT.1886 display EOTF.
Sources: ITU-R BT.709-6 table 1.2; ICC sRGB registry (IEC 61966-2-1).
"""
from __future__ import annotations

from functools import lru_cache
import math

SRGB = "iec61966-2-1"
TRANSFERS = ("bt709", SRGB)


def color_plan(video: dict, enabled: bool, *, ready=True) -> dict:
    if type(enabled) is not bool:
        raise ValueError("normalize_to_srgb must be boolean")
    source = video.get("color_transfer")
    supported = (source in TRANSFERS and video.get("color_primaries") == "bt709"
                 and video.get("color_space") == "bt709" and video.get("color_range") in ("tv", "pc"))
    eligible = ready and supported
    operation = ("blocked" if not eligible else "disabled" if not enabled else
                 "already_srgb" if source == SRGB else "bt709_to_srgb")
    return {"version": 1, "enabled": enabled, "operation": operation,
            "source_transfer": source,
            "working_transfer": (SRGB if enabled else source) if eligible else None,
            "output_transfer": source if eligible else None,
            "working_primaries": "bt709" if eligible else None, "working_format": "rgba8",
            "working_range": "pc", "output_range": "tv", "source_unchanged": True,
            "transform": "nominal_transfer_lut8_v1" if enabled and eligible else None}


def convert_value(value: float, source: str, target: str) -> float:
    """Convert encoded RGB component in [0,1]; no per-frame exposure adjustment."""
    if source not in TRANSFERS or target not in TRANSFERS:
        raise ValueError("Only BT.709 and sRGB SDR transfers are supported")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Expected a finite full-range encoded component")
    if source == target:
        return value
    if source == "bt709":
        linear = value / 4.5 if value < .081 else ((value + .099) / 1.099) ** (1 / .45)
    else:
        linear = value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
    if target == "bt709":
        encoded = 4.5 * linear if linear < .018 else 1.099 * linear ** .45 - .099
    else:
        encoded = 12.92 * linear if linear <= .0031308 else 1.055 * linear ** (1 / 2.4) - .055
    return min(1.0, max(0.0, encoded))


@lru_cache(maxsize=4)
def transfer_lut(source: str, target: str) -> bytes:
    return bytes(math.floor(convert_value(index / 255, source, target) * 255 + .5) for index in range(256))


def convert_rgba8(rgba: bytes, source: str, target: str) -> bytes:
    if len(rgba) % 4:
        raise ValueError("Color conversion requires tightly packed RGBA8")
    lut_bytes = transfer_lut(source, target)  # Also validates identity transfer names.
    if source == target:
        return rgba
    import numpy as np
    pixels = np.frombuffer(rgba, dtype=np.uint8).reshape(-1, 4)
    result = pixels.copy()
    lut = np.frombuffer(lut_bytes, dtype=np.uint8)
    result[:, :3] = lut[pixels[:, :3]]
    return result.tobytes()  # Alpha is not a color component and is never transformed.


def export_transfer_filter(plan: dict | None) -> str:
    """Inverse working->source transfer before existing YUV encoding, on both A/B."""
    if plan is None:  # Pre-normalization manifests remain readable.
        return ""
    if plan.get("version") != 1 or plan.get("operation") == "blocked":
        raise ValueError("Invalid color pipeline in prepared manifest")
    source, target = plan.get("working_transfer"), plan.get("output_transfer")
    if source not in TRANSFERS or target not in TRANSFERS:
        raise ValueError("Unsupported export transfer")
    if source == target:
        return ""
    if source != SRGB or target != "bt709" or plan.get("enabled") is not True:
        raise ValueError("Unexpected working/output color pipeline")
    # Match the Python LUT exactly, including round-to-nearest; keep RGB8 before
    # lutrgb so maxval=255. No shader/toolkit dependency or extra video spool.
    linear = "if(lte(val/maxval,0.04045),val/maxval/12.92,pow((val/maxval+0.055)/1.055,2.4))"
    encoded = f"if(lt({linear},0.018),4.5*{linear},1.099*pow({linear},0.45)-0.099)"
    expression = f"floor(clip({encoded},0,1)*maxval+0.5)"
    return "format=rgba,lutrgb=" + ":".join(f"{component}='{expression}'" for component in "rgb") + ":a=val,"
