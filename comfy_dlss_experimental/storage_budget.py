"""Disk-backed video admission checks; no duration-based 30-second cutoff.

Free-space checks are not reservations: other processes can still consume disk.
Keep a safety floor and recheck periodically during writes. Prepared-cache LRU
eviction is handled separately by storage_manager; saved media is never evicted.
"""
import math
from pathlib import Path
import shutil

MAX_FRAMES = 1_000_000  # External D5V2 header validation, not a duration preset.
DISK_RESERVE = 512 * 1024**2


def require_disk(path, needed, *, stage):
    if type(needed) is not int or needed < 0:
        raise ValueError("Invalid storage estimate")
    existing = Path(path).resolve()
    while not existing.exists():
        parent = existing.parent
        if parent == existing:
            raise OSError("Cannot locate storage filesystem")
        existing = parent
    free = shutil.disk_usage(existing).free
    from .storage_manager import current_job, check_job, GiB
    job = current_job()
    reserve = job["settings"]["free_gib"] * GiB if job else DISK_RESERVE
    check_job()
    required = needed + reserve
    if free < required:
        raise ValueError(f"{stage}磁盘空间不足：预计还需 {needed / 1024**3:.2f} GiB，"
                         f"另留 {reserve / GiB:.2f} GiB 安全余量；可用 {free / 1024**3:.2f} GiB。"
                         "请缩小尺寸、选择较短区间或手动清理已确认不用的缓存。")
    return {"estimated_additional_bytes": needed, "reserve_bytes": reserve,
            "available_bytes": free, "filesystem_path": str(existing), "stage": stage}


def preparation_storage_plan(cache_path, scratch_path, *, frame_count, plane_bytes):
    def existing(path):
        path = Path(path).resolve()
        while not path.exists():
            path = path.parent
        return path
    cache = existing(cache_path)
    # Cache holds color+motion and may also retain an encoded A. Scratch needs
    # raw output and encoded A/B allowances. These are conservative estimates,
    # not a promise of a byte-exact encoder upper bound or a disk reservation.
    cache_bytes = frame_count * (plane_bytes * 3 + 2048)
    if scratch_path is None:
        return {"cache": require_disk(cache, cache_bytes, stage="准备缓存")}
    scratch = existing(scratch_path)
    scratch_bytes = frame_count * plane_bytes * 4
    if cache.stat().st_dev == scratch.stat().st_dev:
        return {"combined": require_disk(cache, cache_bytes + scratch_bytes, stage="缓存与输出")}
    return {"cache": require_disk(cache, cache_bytes, stage="准备缓存"),
            "output": require_disk(scratch, scratch_bytes, stage="输出临时目录")}


def frame_budget(duration, pre_roll, rate, single_frame=False):
    # A single-frame request must not reserve an entire long video.
    count = math.ceil((pre_roll + (0 if single_frame else duration)) * rate) + 4
    if count > MAX_FRAMES:
        raise ValueError("选定区间超过当前 Worker 的 1,000,000 帧协议上限；请分次处理。")
    return count
