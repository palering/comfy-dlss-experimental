"""ComfyUI-specific VIDEO, progress, cancellation and result adapters.

Do not import this module from a standalone caller to manufacture a fake VIDEO.
Use FileVideoSource and an explicit ExecutionContext instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .execution_context import ExecutionContext
from .input_policy import InputColorPolicy
from .media_clip import ClipRequest, probe_media


def comfy_execution_context():
    from .config import data_root
    from comfy.model_management import processing_interrupted
    from comfy.utils import ProgressBar
    import folder_paths

    bar = None

    def progress(_stage, done, total):
        nonlocal bar
        if total:
            if bar is None:
                bar = ProgressBar(total)
            bar.update_absolute(done, total)

    return ExecutionContext(data_root().resolve(), Path(folder_paths.get_temp_directory()).resolve(),
                            processing_interrupted, progress)


@dataclass(frozen=True)
class ComfyVideoSource:
    video: object

    def duration(self, *, color_policy=InputColorPolicy(), cancelled=lambda: False):
        if cancelled():
            raise InterruptedError("DLSS processing cancelled")
        return float(self.video.get_duration())

    def bind(self, request, job, *, color_policy=InputColorPolicy(), cancelled=lambda: False):
        return bind_comfy_video(self.video, request, job, color_policy=color_policy, cancelled=cancelled)


def bind_comfy_video(video, request, job, *, color_policy=InputColorPolicy(), cancelled=None,
                     probe=probe_media):
    """Respect public VIDEO trims/crops; only a known file-backed type is direct."""
    request.validate()
    if cancelled is not None and cancelled():
        raise InterruptedError("DLSS processing cancelled")
    from comfy_api.latest import InputImpl
    if isinstance(video, InputImpl.VideoFromFile):
        source = video.get_stream_source()
        if isinstance(source, (str, Path)):
            source = Path(source).resolve(strict=True)
            metadata = probe(source, color_policy=color_policy, cancelled=cancelled)
            raw = metadata["video"]
            if tuple(video.get_dimensions()) == (raw["width"], raw["height"]):
                offset, _trim_duration = video.get_active_trim_window()
                available = float(video.get_duration())
                duration = min(request.duration, available - request.start)
                if duration <= 0:
                    raise ValueError("Preview starts beyond the VIDEO's active range")
                context = min(request.pre_roll, request.start)
                return source, ClipRequest(offset + request.start, duration, context, request.scale, request.single_frame)
            if metadata.get("input_report", {}).get("assumptions"):
                raise ValueError("带上游裁剪且缺少色彩标签的 VIDEO 需要先对原文件适配；不能先按未知色彩物化再补标签。")
    context = min(request.pre_roll, request.start)
    selected = video.as_trimmed(start_time=request.start - context, duration=request.duration + context)
    if selected is None:
        raise ValueError("Selected VIDEO range is empty")
    from .storage_manager import current_job, MiB
    storage = current_job()
    if storage is not None:
        # Public VIDEO.save_to has no bounded/cancellable sink. Keep the existing
        # quota guard and do not turn this adapter into a full-video decoder.
        width, height = video.get_dimensions()
        selected_frames = selected.get_frame_count()
        if type(selected_frames) is not int or selected_frames <= 0:
            raise ValueError("Cannot bound VIDEO materialization; save and reload the upstream video first")
        if width * height * 8 * selected_frames > storage["settings"]["entry_mib"] * MiB:
            raise ValueError("Large cropped/tensor VIDEO must first be saved and loaded as a file-backed VIDEO for bounded streaming. The public save_to fallback cannot enforce a disk quota.")
    destination = job / "selected-input.mp4"
    selected.save_to(str(destination), crf=0, preset="ultrafast")
    if cancelled is not None and cancelled():
        raise InterruptedError("DLSS processing cancelled")
    return destination, ClipRequest(context, request.duration, context, request.scale, request.single_frame)
