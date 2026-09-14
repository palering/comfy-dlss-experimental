"""Offline FG/MFG timing and capability contracts, not a registered renderer.

Pure bounded metadata planning: no interpolation, copied pixels or claimed GPU
support. An actual backend must deliver every requested generated image before
these slots may be encoded. Displayed-frame counters are not such evidence.
"""
from dataclasses import dataclass

MAX_TIMESTAMP_NS = (1 << 63) - 1

# Scoped to the tested Present-capture path, not a universal hardware capability
# or a live focus check. There is no registered FG node/renderer yet.
FG_FOREGROUND_NOTICE = (
    "FG_FOREGROUND_REQUIRED: The currently tested FG Present-capture path requires "
    "the Worker window on the GPU host to remain foreground and focused, not the "
    "ComfyUI browser. Background FG is not verified. eOff disables generation; "
    "eRetainResourcesWhenOff only retains resources while disabled."
)


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {name}")
    return value


@dataclass(frozen=True)
class FGSettings:
    # Output multiplier, not number of generated frames. 2x -> one new frame.
    multiplier: int = 2

    def __post_init__(self):
        _integer(self.multiplier, "FG multiplier", 2, 6)

    @property
    def generated_per_interval(self):
        return self.multiplier - 1

    @property
    def feature(self):
        return "fg" if self.multiplier == 2 else "mfg"


@dataclass(frozen=True)
class FGCapability:
    adapter_supported: bool
    max_generated_frames: int | None
    offline_output_verified: bool = False

    def __post_init__(self):
        if type(self.adapter_supported) is not bool or type(self.offline_output_verified) is not bool:
            raise ValueError("FG capability booleans must be explicit")
        if self.max_generated_frames is not None:
            _integer(self.max_generated_frames, "runtime FG frame limit", 0, 64)
        if not self.adapter_supported and self.offline_output_verified:
            raise ValueError("An unsupported adapter cannot have verified FG output")

    def require(self, settings, *, offline=True):
        if not isinstance(settings, FGSettings):
            raise TypeError("FGSettings required")
        if not self.adapter_supported:
            raise ValueError("The selected adapter/runtime does not support DLSS Frame Generation")
        if self.max_generated_frames is None:
            raise ValueError("The runtime's maximum generated-frame count has not been queried")
        if settings.generated_per_interval > self.max_generated_frames:
            raise ValueError(f"Requested {settings.multiplier}x exceeds runtime limit "
                             f"{self.max_generated_frames + 1}x; no emulated MFG fallback")
        if offline and not self.offline_output_verified:
            raise ValueError("FG display support is not verified offline generated-frame output. "
                             + FG_FOREGROUND_NOTICE)


@dataclass(frozen=True)
class FrameSlot:
    pts_ns: int
    duration_ns: int
    origin: str
    left_source: int
    right_source: int | None = None
    phase_numerator: int = 0
    phase_denominator: int = 1


class FGTimeline:
    """Plan at most six output slots per input interval, with one-frame lookahead.

    A cut holds the last real image until the next real image: no cross-cut
    interpolation. finish(end_pts_ns) preserves the final real image's duration,
    rather than inventing a future frame or changing audio speed. Output is VFR
    metadata where needed; a CFR exporter must declare a separate hold policy.
    """
    def __init__(self, settings):
        if not isinstance(settings, FGSettings):
            raise TypeError("FGSettings required")
        self.settings = settings
        self._pending = None
        self._closed = False

    def push(self, source_index, pts_ns, *, scene_cut=False):
        if self._closed:
            raise RuntimeError("FG timeline is closed")
        _integer(source_index, "source index", 0, MAX_TIMESTAMP_NS)
        _integer(pts_ns, "source PTS", 0, MAX_TIMESTAMP_NS)
        if type(scene_cut) is not bool:
            raise ValueError("scene_cut must be boolean")
        if self._pending is None:
            if source_index != 0:
                raise ValueError("FG sequence must start at source index zero")
            self._pending = (source_index, pts_ns)
            return ()
        previous_index, previous_pts = self._pending
        if source_index != previous_index + 1 or pts_ns <= previous_pts:
            raise ValueError("FG needs contiguous source indices and increasing PTS")
        count = 1 if scene_cut else self.settings.multiplier
        interval = pts_ns - previous_pts
        if interval < count:
            raise ValueError("FG output timestamps would collide at nanosecond precision")
        stamps = tuple(previous_pts + (interval * part) // count for part in range(count)) + (pts_ns,)
        slots = tuple(FrameSlot(stamps[part], stamps[part + 1] - stamps[part],
            "rendered" if part == 0 else "generated", previous_index,
            None if part == 0 else source_index, part, count if part else 1)
            for part in range(count))
        self._pending = (source_index, pts_ns)
        return slots

    def finish(self, end_pts_ns):
        if self._closed:
            raise RuntimeError("FG timeline is closed")
        _integer(end_pts_ns, "sequence end PTS", 0, MAX_TIMESTAMP_NS)
        if self._pending is None:
            raise ValueError("Cannot finish an empty FG sequence")
        index, pts = self._pending
        if end_pts_ns <= pts:
            raise ValueError("Sequence end must be after the last source PTS")
        self._closed = True
        return (FrameSlot(pts, end_pts_ns - pts, "rendered", index),)
