"""VIDEO + source-bound camera/depth -> shared CXR1 streaming execution.

No Comfy imports, estimator or guessed projection in the execution core. The
single Comfy endpoint below only validates graph ownership and wraps the file.
"""
from __future__ import annotations

import contextlib
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from pathlib import Path

from .camera_provider import CameraProvider, validate_camera_depth
from .execution_context import with_execution_context
from .execution_log import current_trace, tracked
from .external_guides import ExternalGuideProvider, reopen_external_guide
from .feature_requirements import requirements_report
from .input_policy import InputColorPolicy
from .media_clip import ClipRequest, file_hash
from .media_pipeline import require_pipeline
from .media_tools import media_tools_scope
from .owned_adapter import rgba8_to_half
from .sl_contract import ReconstructionSettings
from .sl_execution import ReconstructionFrame, reconstruction_job, render_reconstruction_frames
from .sl_runtime import validate_sl_runtime
from .sr_dimensions import output_budget_report
from .storage_manager import check_job, managed_job
from .stream_media import inspect_stream, prepared_frames
from .video_pipeline import bind_video_source, check_cancel, guide_settings
from .video_range import resolve_video_range
from .video_source import VideoSource


@dataclass(frozen=True)
class SLVideoInput:
    sequence: dict
    depth: ExternalGuideProvider
    camera: CameraProvider
    attachment_roles: tuple[str, ...] = ("depth", "camera")

    def __post_init__(self):
        if not isinstance(self.sequence, dict) or "video" in self.sequence:
            raise ValueError("Streamline execution metadata must not contain a VIDEO object")
        validate_camera_depth(self.camera, self.depth)
        roles = tuple(self.attachment_roles)
        if len(set(roles)) != len(roles) or not {"depth", "camera"} <= set(roles):
            raise ValueError("Streamline input must declare unique depth and camera roles")
        requirements_report("owned_cxr1_sl", "sr", roles)
        object.__setattr__(self, "sequence", deepcopy(self.sequence))
        object.__setattr__(self, "depth", replace(self.depth, source_video=None))
        object.__setattr__(self, "camera", replace(self.camera, source_video=None))
        object.__setattr__(self, "attachment_roles", roles)


def sl_input_from_pipeline(pipeline):
    pipeline = require_pipeline(pipeline)
    if pipeline.stages:
        raise ValueError("Streamline requires an input-only pipeline; render other stages and reload first")
    sequence = pipeline.sequence_copy()
    attachments = {a.role: a for a in pipeline.attachments}
    for role, kind in (("depth", ExternalGuideProvider), ("camera", CameraProvider)):
        item = attachments.get(role)
        if item is None or not isinstance(item.payload, kind):
            raise ValueError(f"Streamline requires an explicit numerical {role} provider")
        if (item.payload.source_video is not sequence["video"] or item.semantics != item.payload.semantics):
            raise ValueError("Streamline camera/depth must belong to this exact VIDEO and declared view")
    return SLVideoInput({k: v for k, v in sequence.items() if k != "video"},
                        attachments["depth"].payload, attachments["camera"].payload, tuple(attachments))


def _preflight(manifest, camera, depth, cancelled):
    if ((manifest["width"], manifest["height"]) != (camera.width, camera.height)
            or (manifest["metadata"]["video"]["width"], manifest["metadata"]["video"]["height"]) != (camera.width, camera.height)):
        raise ValueError("Decoded VIDEO, camera and depth must share the full unmodified source grid")
    if manifest["source_sha256"] != camera.source_sha256 or file_hash(Path(manifest["source"])) != camera.source_sha256:
        raise ValueError("Streamline source SHA-256 differs from camera/depth; rebuild for the actual VIDEO view")
    if Fraction(manifest["fps"]) != Fraction(camera.fps):
        raise ValueError("Streamline camera FPS differs from the decoded CFR source")
    if manifest.get("color_pipeline", {}).get("working_transfer") != "iec61966-2-1":
        raise ValueError("Streamline SDR VIDEO requires sRGB working color; enable normalize_to_srgb in Video Input Adapter")
    if manifest["visible_start_index"] > 120:
        raise ValueError("Streamline history is limited to 120 frames; reduce History Settings pre-roll")
    indices = []
    root = depth.manifest_path.parent.resolve()
    for frame in manifest["frames"]:
        check_cancel(cancelled)
        ci, di = camera.index_at(frame["pts_ns"]), depth.index_at(frame["pts_ns"])
        if indices and ci != indices[-1][0] + 1:
            raise ValueError("Streamline camera timeline has a gap in the requested range")
        path = (root / depth.files[di].path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Requested depth frame is missing or escapes its manifest directory")
        indices.append((ci, di))
    if not indices:
        raise ValueError("No Streamline source frames selected")
    return indices


@with_execution_context
@tracked("process")
@managed_job
def render_sl_video(*, source, context, input_data, runtime, mode="quality", output_width=960,
                    output_height=540, start_time=0, duration=0, process_to_end=True,
                    pre_roll=.5, single_frame=False):
    validate_sl_runtime(runtime)
    if not isinstance(source, VideoSource) or not isinstance(input_data, SLVideoInput):
        raise TypeError("Streamline VIDEO requires VideoSource and SLVideoInput")
    if type(single_frame) is not bool:
        raise ValueError("single_frame must be boolean")
    sequence = deepcopy(input_data.sequence)
    if sequence.get("input_report", {}).get("ready") is False:
        raise ValueError("Video Input Adapter must pass validation before Streamline rendering")
    trace = current_trace()
    cancelled = lambda: context.cancelled() or trace.release_requested.is_set()
    check_cancel(cancelled)
    camera = input_data.camera.reopen(cancelled)
    depth = reopen_external_guide(input_data.depth.snapshot_config())
    if depth.cache_identity != input_data.depth.cache_identity:
        raise ValueError("Streamline depth manifest changed after input assembly")
    validate_camera_depth(camera, depth)
    public = sequence.get("public", {})
    if (public.get("width"), public.get("height")) != (camera.width, camera.height):
        raise ValueError("Input declarations differ from the camera source extent")
    if mode == "dlaa":
        output_width, output_height = camera.width, camera.height
    native = ReconstructionSettings(camera.width, camera.height, output_width, output_height,
                                    mode=mode, depth_inverted=camera.depth_inverted)
    native.encode()
    if output_width % 2 or output_height % 2:
        raise ValueError("Streamline output dimensions must be even for YUV420 export")
    guides = guide_settings(sequence.get("settings", {}))
    with media_tools_scope(sequence.get("media_tools_config")):
        import av  # noqa: F401
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        policy = InputColorPolicy.from_dict(sequence.get("color_policy"))
        total = source.duration(color_policy=policy, cancelled=cancelled)
        chosen = resolve_video_range(total, start_time, duration, process_to_end, legacy_zero=True)
        request = ClipRequest(start_time, chosen, pre_roll, 1.0, single_frame)
        request.validate()
        job = reconstruction_job(context)
        path, bound = bind_video_source(source, request, job, color_policy=policy, cancelled=cancelled)
        manifest = inspect_stream(path, bound, guides, policy, cancelled, context.progress)
        indices = _preflight(manifest, camera, depth, cancelled)
        available = set(input_data.attachment_roles) | {"color", "motion", "timeline", "frame_metadata"}
        if manifest["metadata"]["has_audio"]:
            available.add("audio")
        required = requirements_report("owned_cxr1_sl", "dlaa" if mode == "dlaa" else "sr", available)
        if required["missing_required"]:
            raise ValueError("Missing Streamline inputs: " + ", ".join(required["missing_required"]))
        output_manifest = deepcopy(manifest)
        output_manifest.update(width=output_width, height=output_height)
        output_manifest["metadata"]["video"]["color_transfer"] = "iec61966-2-1"
        output_manifest["color_pipeline"] = {"version": 1, "operation": "already_srgb", "enabled": True,
                                            "working_transfer": "iec61966-2-1", "output_transfer": "iec61966-2-1"}

        def frames():
            # Recheck immediately before the second decode, including DIS/zero.
            if file_hash(path) != camera.source_sha256:
                raise ValueError("VIDEO content changed between timing scan and Streamline rendering")
            with contextlib.closing(prepared_frames(manifest, guides, cancelled)) as prepared:
                for index, (color, motion, info) in enumerate(prepared):
                    if index >= len(indices):
                        raise ValueError("Decoded frame count changed after Streamline timing scan")
                    ci, di = indices[index]
                    record = camera.frames[ci]
                    plane = depth.read_frame(di, source_sha256=camera.source_sha256, view_id="source", pts_ns=depth.files[di].pts_ns)
                    data = plane.data if plane.dtype == "float32_le" else plane.as_numpy().astype("<f4").tobytes()
                    metadata = replace(record.metadata, reset=record.metadata.reset or bool(info.get("reset")) or depth.files[di].reset)
                    yield ReconstructionFrame(info["pts_ns"], rgba8_to_half(color),
                                              motion, data, record.camera, metadata)

        visible = manifest["visible_start_index"]
        result = render_reconstruction_frames(context=context, runtime=runtime, native=native,
            manifest=output_manifest, frames=frames(), job=job, cancelled=cancelled,
            input_report={"video_input": {"source_sha256": camera.source_sha256, "camera": camera.report(),
                "depth": depth.report(), "guide_mode": guides.motion_provider, "guide_settings": asdict(guides),
                "output_budget": output_budget_report(output_width, output_height), "requirements": required,
                "unconsumed_attachments": required["unconsumed"], "color_pipeline": manifest["color_pipeline"],
                "source_size": [camera.width, camera.height], "single_frame": single_frame}},
            range_report={"start_frame": indices[visible][0], "end_frame": indices[-1][0] + 1,
                "history_start": indices[0][0], "start_time": start_time, "range_duration": chosen,
                "input_duration": total, "process_to_end": process_to_end})
        check_cancel(cancelled)
        check_job()
        return result


def run_sl_process(*, pipeline, **kwargs):
    from .comfy_adapter import ComfyVideoSource, comfy_execution_context
    from comfy_api.latest import InputImpl
    data = sl_input_from_pipeline(pipeline)
    output, report = render_sl_video(source=ComfyVideoSource(pipeline.sequence_copy()["video"]),
                                    context=comfy_execution_context(), input_data=data, **kwargs)
    return InputImpl.VideoFromFile(str(output)), report
