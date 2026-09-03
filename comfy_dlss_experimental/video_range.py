"""Resolve UI ranges against the actual upstream VIDEO, never a guessed length."""
import math


def resolve_video_range(total, start, duration, to_end=False, *, legacy_zero=False):
    if type(to_end) is not bool:
        raise ValueError("process_to_end must be a boolean")
    for name, value in (("video duration", total), ("start", start), ("duration", duration)):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid {name}")
    if total <= start:
        raise ValueError("处理起点必须早于输入 VIDEO 的末尾")
    remaining = total - start
    chosen = remaining if to_end or (legacy_zero and duration == 0) else min(duration, remaining)
    if chosen <= 0:
        raise ValueError("请指定有效时长，或勾选处理到视频末尾")
    return chosen
