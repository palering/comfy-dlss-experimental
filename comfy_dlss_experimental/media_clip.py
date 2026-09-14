"""Bounded SDR clip preparation and encoding; no vendor or Comfy imports.

The first implementation intentionally rejects VFR, HDR, non-square pixels,
and rotation instead of silently normalizing timing or color. Frames live on
disk, not in a whole-video tensor. The caller owns an exclusive job directory.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .temporal_guides import GuideSettings, TemporalGuideGenerator
from .input_policy import InputColorPolicy, validated_metadata
from .color_normalization import convert_rgba8, export_transfer_filter
from .execution_log import phase
from .storage_budget import frame_budget, require_disk, preparation_storage_plan
from .media_tools import media_executable


@dataclass(frozen=True)
class ClipRequest:
    start: float = 1.0
    duration: float = 2.0
    pre_roll: float = 0.5
    scale: float = 1.0
    single_frame: bool = False

    def validate(self):
        if type(self.single_frame) is not bool:
            raise ValueError("single_frame must be a boolean")
        for name, low, high in (("start", 0, 86400), ("duration", 0.000001 if self.single_frame else 0.04, 86400),
                                ("pre_roll", 0, 5), ("scale", 0.25, 1)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid clip {name}")


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def run_cancellable(command: list[str], *, timeout: float, cancelled) -> subprocess.CompletedProcess:
    """Cancel and reap the exact child; never use shell-wide process matching."""
    if cancelled():
        raise InterruptedError("Media operation cancelled")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancelled():
                raise InterruptedError("Media operation cancelled")
            if time.monotonic() >= deadline:
                raise TimeoutError("Media operation timed out")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError(f"Media command failed ({process.returncode}): {stderr[-4000:]}")
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def inspect_media(source: Path, ffprobe: str | None = None, *, cancelled=None) -> dict:
    """Read selected media headers without rejecting missing/unsupported tags."""
    command = [
        ffprobe or media_executable("ffprobe"), "-v", "error", "-show_entries",
        "format=format_name,duration,size,bit_rate,start_time:"
        "stream=index,codec_type,codec_name,profile,level,width,height,avg_frame_rate,r_frame_rate,"
        "start_time,duration,nb_frames,bit_rate,bits_per_raw_sample,pix_fmt,color_space,color_range,"
        "color_transfer,color_primaries,sample_aspect_ratio,display_aspect_ratio,sample_rate,channels,channel_layout:"
        "stream_side_data=side_data_type,rotation",
        "-of", "json", str(source.resolve(strict=True)),
    ]
    result = (subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
              if cancelled is None else run_cancellable(command, timeout=30, cancelled=cancelled))
    return json.loads(result.stdout)


def probe_media(source: Path, ffprobe: str | None = None, *, cancelled=None,
                color_policy: InputColorPolicy = InputColorPolicy()) -> dict:
    return validated_metadata(inspect_media(source, ffprobe, cancelled=cancelled), color_policy)


def validate_cfr(timestamps: list[int], rate: Fraction):
    if not timestamps:
        raise ValueError("the selected range contains no frames")
    step = Fraction(1_000_000_000, 1) / rate
    for a, b in zip(timestamps, timestamps[1:]):
        if b <= a or abs(Fraction(b - a) - step) > 100_000:
            raise ValueError("variable/discontinuous frame timing is not supported; timestamps were not rewritten")


def prepare_clip(source: Path, directory: Path, request: ClipRequest,
                 guides: GuideSettings = GuideSettings(), *, cancelled: Callable[[], bool] = lambda: False,
                 color_policy: InputColorPolicy = InputColorPolicy(), scratch_path: Path | None = None) -> dict:
    request.validate()
    guides.validate()
    source = source.resolve(strict=True)
    metadata = probe_media(source, cancelled=cancelled, color_policy=color_policy)
    import av
    from av.video.reformatter import VideoReformatter
    video = metadata["video"]
    color_pipeline = metadata["input_report"]["color_normalization"]
    width, height = (int(video[key] * request.scale) // 2 * 2 for key in ("width", "height"))
    if min(width, height) < 64 or width > 7680 or height > 4320:
        raise ValueError("unsupported clip dimensions")
    rate = Fraction(metadata["fps"])
    max_frames = frame_budget(request.duration, request.pre_roll, rate, request.single_frame)
    # Two raw planes plus an allowance for per-frame manifest metadata. Pixel
    # working memory remains per-frame, even when the selected duration is long.
    storage = preparation_storage_plan(directory, scratch_path, frame_count=max_frames, plane_bytes=width * height * 4)
    directory.mkdir(parents=True, exist_ok=False)
    start = Fraction(str(request.start))
    finish = start + Fraction(str(request.duration))
    read_start = max(Fraction(0), start - Fraction(str(request.pre_roll)))
    reformatter = VideoReformatter()
    frames = []
    source_sha256 = file_hash(source)
    source_identity = {"sha256": source_sha256, "width": video["width"], "height": video["height"]}
    with TemporalGuideGenerator(width, height, guides, cancelled=cancelled, source_identity=source_identity) as temporal, av.open(str(source)) as container, (directory / "color.rgba").open("xb") as colors, (directory / "motion.rg16f").open("xb") as motions:
        stream = next(s for s in container.streams.video if s.index == video["index"])
        origin = Fraction(stream.start_time or 0) * stream.time_base
        if read_start > 0:
            container.seek(int((origin + read_start) / stream.time_base), stream=stream, backward=True)
        for frame in container.decode(stream):
            if cancelled():
                raise InterruptedError("clip preparation cancelled")
            if frame.pts is None:
                raise ValueError("source frame has no timestamp")
            relative = Fraction(frame.pts) * frame.time_base - origin
            if relative < read_start:
                continue
            if relative >= finish:
                break
            if len(frames) >= max_frames:
                raise ValueError("frame count exceeded preview budget")
            if len(frames) % 64 == 0:
                require_disk(directory, (max_frames - len(frames)) * (width * height * 8 + 2048), stage="颜色/光流缓存")
            if frame.width != video["width"] or frame.height != video["height"]:
                raise ValueError("midstream resolution changes are not supported")
            # Matrix/range conversion only: keep nonlinear sRGB/709 code values.
            # Do not linearize and then accidentally apply a second gamma curve.
            with phase("color_conversion"):
                rgba = reformatter.reformat(frame, width=width, height=height, format="rgba",
                                             src_colorspace="ITU709", dst_colorspace="ITU709",
                                             src_color_range="MPEG" if video["color_range"] == "tv" else "JPEG",
                                             dst_color_range="JPEG").to_ndarray().tobytes()
                if color_pipeline["operation"] == "bt709_to_srgb":
                    rgba = convert_rgba8(rgba, color_pipeline["source_transfer"], color_pipeline["working_transfer"])
            with phase("optical_flow"):
                motion, guide = temporal.process(rgba, pts_ns=round(relative * 1_000_000_000))
            with phase("cache_write"):
                colors.write(rgba)
                motions.write(motion)
            frames.append({"pts_ns": round(relative * 1_000_000_000), "visible": relative >= start,
                           "color_sha256": hashlib.sha256(rgba).hexdigest(),
                           "motion_sha256": hashlib.sha256(motion).hexdigest(), **guide})
            if request.single_frame and relative >= start:
                break
    validate_cfr([f["pts_ns"] for f in frames], rate)
    metadata["input_report"]["decode_validation"] = "selected_range_passed"
    metadata["input_report"]["timing_validation"] = "selected_range_cfr_passed"
    metadata["input_report"]["validated_request"] = asdict(request)
    metadata["input_report"]["color_normalization"]["execution"] = "selected_range_prepared"
    visible = [i for i, frame in enumerate(frames) if frame["visible"]]
    if not visible:
        raise ValueError("no visible frame after pre-roll")
    manifest = {"schema_version": 1, "source": str(source), "source_sha256": source_sha256, "storage_plan": storage,
                "request": asdict(request), "guide_settings": asdict(guides), "metadata": metadata,
                "width": width, "height": height, "fps": str(rate), "frames": frames,
                "visible_start_index": visible[0], "visible_count": len(visible),
                "motion_convention": "current_to_previous_pixels_xy_top_left",
                "color_pipeline": color_pipeline,
                "color_contract": ("nonlinear_srgb_rgba8_full_range_v1" if color_policy.normalize_to_srgb else
                                   "nonlinear_rgba8_full_range_no_transfer_conversion")}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def export_video(raw: Path, destination: Path, manifest: dict, *, with_audio: bool = True,
                 cancelled: Callable[[], bool] = lambda: False):
    """Encode a completed visible-only RGBA spool. No silent partial output reuse."""
    width, height, count = manifest["width"], manifest["height"], manifest["visible_count"]
    if raw.stat().st_size != width * height * 4 * count:
        raise ValueError("encoded spool has an unexpected size")
    if destination.exists():
        raise FileExistsError(destination)
    temporary = destination.with_name(destination.stem + ".partial.mp4")
    if temporary.exists():
        raise FileExistsError(temporary)
    rate = Fraction(manifest["fps"])
    require_disk(destination.parent, raw.stat().st_size, stage="视频编码输出")
    first = manifest["frames"][manifest["visible_start_index"]]["pts_ns"] / 1e9
    command = [media_executable("ffmpeg"), "-hide_banner", "-v", "error", "-n", "-f", "rawvideo", "-pixel_format", "rgba",
               "-video_size", f"{width}x{height}", "-framerate", str(rate), "-i", str(raw)]
    audio = with_audio and manifest["metadata"]["has_audio"]
    if audio:
        command += ["-ss", f"{first:.9f}", "-i", manifest["source"]]
    command += ["-map", "0:v:0"]
    if audio:
        # Match the streaming encoder: end the decoded audio range explicitly,
        # without rebasing away source offsets or relying on AAC packet size.
        command += ["-map", "1:a:0", "-af", f"atrim=end={float(count / rate):.9f}", "-c:a", "aac", "-b:a", "160k"]
    transfer = manifest["metadata"]["video"]["color_transfer"]
    # Set frame metadata as well as codec options: FFmpeg 9/libx264 can otherwise
    # replace codec-level transfer/primaries with the raw input's unspecified tags.
    color_pipeline = manifest.get("color_pipeline")
    if color_pipeline is not None and color_pipeline.get("output_transfer") != transfer:
        raise ValueError("Color pipeline and output metadata disagree")
    color_filter = export_transfer_filter(color_pipeline) + ("scale=in_range=full:out_range=limited:out_color_matrix=bt709,format=yuv420p,"
                    f"setparams=range=limited:color_primaries=bt709:color_trc={transfer}:colorspace=bt709")
    command += ["-vf", color_filter,
                "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-threads", "4",
                "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
                "-color_trc", transfer,
                "-t", f"{float(count / rate):.9f}", "-map_metadata", "-1", "-movflags", "+faststart", str(temporary)]
    duration_seconds = float(count / rate)
    run_cancellable(command, timeout=max(120, min(86400, duration_seconds * 10 + 60)), cancelled=cancelled)
    # Verify the actual encoded frame count before publishing the final name.
    check = run_cancellable([media_executable("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_frames",
                            "-show_entries", "stream=nb_read_frames", "-of", "json", str(temporary)],
                           timeout=max(30, min(86400, duration_seconds * 2 + 30)), cancelled=cancelled)
    if int(json.loads(check.stdout)["streams"][0]["nb_read_frames"]) != count:
        raise ValueError("encoder frame count mismatch")
    encoded = probe_media(temporary, cancelled=cancelled)
    if (encoded["video"]["color_transfer"] != transfer or encoded["video"]["color_range"] != "tv"
            or encoded["fps"] != str(rate) or encoded["has_audio"] != audio):
        raise ValueError("encoder changed color, frame rate or audio contract")
    temporary.rename(destination)
