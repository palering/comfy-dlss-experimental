"""Small source/view interface shared by file jobs and host adapters.

Frame content hashes and actual PTS remain validated by the media pipeline.
The file source only represents a temporal view; spatial crops must be supplied
as an explicitly transformed source, never hidden by returning its original file.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Protocol, runtime_checkable

from .input_policy import InputColorPolicy
from .media_clip import ClipRequest, probe_media


@runtime_checkable
class VideoSource(Protocol):
    def duration(self, *, color_policy: InputColorPolicy, cancelled) -> float: ...

    def bind(self, request: ClipRequest, job: Path, *,
             color_policy: InputColorPolicy, cancelled) -> tuple[Path, ClipRequest]: ...


@dataclass(frozen=True)
class FileVideoSource:
    path: Path
    trim_start: float = 0.0
    trim_duration: float | None = None

    def __post_init__(self):
        path = Path(self.path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("Video source must be a regular file")
        object.__setattr__(self, "path", path)
        if (type(self.trim_start) not in (int, float) or not math.isfinite(self.trim_start)
                or not 0 <= self.trim_start < 86400):
            raise ValueError("Invalid file source trim start")
        if self.trim_duration is not None and (type(self.trim_duration) not in (int, float)
                or not math.isfinite(self.trim_duration) or not 0 < self.trim_duration <= 86400):
            raise ValueError("Invalid file source trim duration")

    def duration(self, *, color_policy=InputColorPolicy(), cancelled=lambda: False):
        if cancelled():
            raise InterruptedError("DLSS processing cancelled")
        metadata = probe_media(self.path, color_policy=color_policy, cancelled=cancelled)
        # Prefer the video stream duration, not an audio track's longer extent.
        value = metadata["video"].get("duration")
        if value in (None, "N/A", ""):
            value = metadata["input_report"].get("format", {}).get("duration")
        try:
            total = float(value)
        except (ValueError, TypeError) as error:
            raise ValueError("File video duration is unavailable") from error
        available = total - self.trim_start
        if not math.isfinite(total) or total <= 0 or available <= 0:
            raise ValueError("File source trim starts beyond a finite video duration")
        return min(available, self.trim_duration) if self.trim_duration is not None else available

    def bind(self, request, job, *, color_policy=InputColorPolicy(), cancelled=lambda: False):
        request.validate()
        available = self.duration(color_policy=color_policy, cancelled=cancelled)
        duration = min(request.duration, available - request.start)
        if duration <= 0:
            raise ValueError("Requested range starts beyond the file source's active view")
        # History context must not reveal frames excluded by the upstream trim.
        context = min(request.pre_roll, request.start)
        return self.path, ClipRequest(self.trim_start + request.start, duration, context,
                                      request.scale, request.single_frame)
