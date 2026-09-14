"""Host-neutral NR file execution; ComfyUI is one adapter, not the executor.

The source is a VideoSource, metadata is the existing version-2 input contract,
and results are a Path plus a report. No VIDEO, ProgressBar, folder_paths or
Comfy server object is consumed here. Existing media/Worker contracts remain
unchanged; this is not yet a versioned external CLI or frame-stream service.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import uuid

from .execution_context import with_execution_context
from .execution_log import current_trace, tracked
from .input_policy import InputColorPolicy
from .media_clip import ClipRequest
from .media_tools import with_media_tools
from .processing_plan import compile_legacy_nr
from .storage_manager import attach_job, check_job, managed_job
from .video_pipeline import (bind_video_source, check_cancel, guide_settings, prepared_cache,
                             profile_settings, release_prepared_cache, render_variant)
from .video_range import resolve_video_range
from .video_source import VideoSource


def validate_nr_inputs(sequence, runtime, contract, profiles):
    if sequence.get("input_report") and not sequence["input_report"]["ready"]:
        raise ValueError("视频输入适配未通过：" + "；".join(i["message"] for i in sequence["input_report"]["issues"]))
    if not runtime.get("ready") or runtime.get("backend") not in {"direct_nr", "owned_nr"}:
        raise ValueError("This video path requires a ready direct_nr or owned_nr Runtime Configuration")
    if sequence.get("schema_version") != 2:
        raise ValueError("Recreate Prepare DLSS Video with the current node definitions")
    if contract.get("schema_version") != 2 or contract.get("mode") != "native":
        raise ValueError("Use current DLSS History Settings; SR/DLAA quality presets are not applicable")
    for effect in profiles:
        if effect is not None:
            leaf_profiles, _plan = compile_legacy_nr(effect).legacy_parts()
            for profile in leaf_profiles:
                settings = profile_settings(profile, 64, 64, 1, contract["warmup_frames"])
                if runtime["backend"] == "owned_nr":
                    from .owned_adapter import owned_settings
                    owned_settings(settings)
    return guide_settings(sequence["settings"])


@with_execution_context
@tracked("process")
@with_media_tools
@managed_job
def render_nr_video(*, source, context, sequence, runtime, contract, profile, start_time=0,
                    duration=0, scale=100, process_to_end=True, retain_prepared_cache=True):
    if not isinstance(source, VideoSource):
        raise TypeError("NR file execution requires a VideoSource adapter")
    if "video" in sequence:
        raise ValueError("Pass the video through source, not inside execution metadata")
    if type(retain_prepared_cache) is not bool:
        raise ValueError("retain_prepared_cache must be boolean")
    trace = current_trace()
    cancelled = lambda: trace.release_requested.is_set() or context.cancelled()
    check_cancel(cancelled)
    if sequence.get("pipeline_report"):
        trace.record["input_pipeline"] = sequence["pipeline_report"]
    guides = validate_nr_inputs(sequence, runtime, contract, [profile])
    _profiles, effect_plan = compile_legacy_nr(profile).legacy_parts()
    trace.record["nr_effect"] = effect_plan
    policy = InputColorPolicy.from_dict(sequence.get("color_policy"))
    total = source.duration(color_policy=policy, cancelled=cancelled)
    chosen_duration = resolve_video_range(total, start_time, duration, process_to_end, legacy_zero=True)
    trace.record["resolved_range"] = {"input_duration": total, "start_time": start_time,
                                      "range_duration": chosen_duration, "process_to_end": process_to_end}
    request = ClipRequest(start_time, chosen_duration, contract["pre_roll"], scale / 100)
    request.validate()
    job = context.temp_root / "dlss-experimental" / ("render-" + uuid.uuid4().hex)
    job.mkdir(parents=True, exist_ok=False)
    attach_job(job)
    check_job()
    source_path, bound = bind_video_source(source, request, job, color_policy=policy, cancelled=cancelled)
    cache_root = context.data_root
    prepared, manifest, reused = prepared_cache(
        source_path, bound, guides, cache_root, cancelled, lambda *_: None,
        color_policy=policy, scratch_path=job, lease=True, retain=retain_prepared_cache)
    prepared_lease = True
    try:
        trace.record["source_path"] = str(source_path)
        trace.record["source_sha256"] = manifest["source_sha256"]
        trace.record["guide_cache_hit"] = reused
        trace.record["storage_plan"] = manifest.get("storage_plan")
        trace.record["flow_backend"] = manifest.get("flow_backend")
        trace.report_file = job / "result" / "report.json"
        context.progress("rendering_nr", 0, len(manifest["frames"]) * effect_plan["pass_count"])
        output, report = render_variant(prepared, manifest, runtime, profile, cache_root, job / "result",
                                        contract["warmup_frames"], cancelled, context.progress)
        check_cancel(cancelled)
        if sequence.get("pipeline_report"):
            report["input_pipeline"] = sequence["pipeline_report"]
        retention = release_prepared_cache(prepared, cache_root, retain=retain_prepared_cache, success=True)
        if manifest.get("streaming"):
            retention = {"policy": "streaming_no_prepared_cache", "removed": False, "cache_created": False}
        prepared_lease = False
        trace.record["prepared_cache_retention"] = retention
        report["prepared_cache_retention"] = retention
        report["guide_cache_hit"] = reused
        report["frame_count"] = manifest["visible_count"]
        report["input_report"] = manifest["metadata"].get("input_report")
        report["guide_mode"] = guides.motion_provider
        report["guide_settings"] = asdict(guides)
        report["output_path"] = str(output)
        (job / "result" / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return output, report
    finally:
        if prepared_lease:
            try:
                trace.record["prepared_cache_retention"] = release_prepared_cache(
                    prepared, cache_root, retain=retain_prepared_cache, success=False)
            except BaseException as cleanup_error:
                trace.record["prepared_cache_retention"] = {
                    "policy": "retain" if retain_prepared_cache else "discard_after_success",
                    "removed": False, "error": f"{type(cleanup_error).__name__}: {cleanup_error}"}
