"""Thin Comfy integration over the independently tested video pipeline."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
import time
import uuid
from fractions import Fraction

from .config import data_root
from .media_clip import ClipRequest
from .input_policy import InputColorPolicy
from .execution_log import tracked, current_trace
from .video_range import resolve_video_range
from .media_tools import with_media_tools, media_tools_scope
from .preview import preview_sessions, send_preview_event, _source_video_view
from .nr_effect import expand_effect
from .storage_manager import managed_job, attach_job, check_job
from .video_pipeline import (bind_video_source, check_cancel, guide_settings, prepared_cache,
                             profile_settings, release_prepared_cache, render_original, render_variant)


def interrupted() -> bool:
    from comfy.model_management import processing_interrupted
    return processing_interrupted()


def validate_inputs(sequence, runtime, contract, profiles):
    if sequence.get("input_report") and not sequence["input_report"]["ready"]:
        raise ValueError("视频输入适配未通过：" + "；".join(i["message"] for i in sequence["input_report"]["issues"]))
    if not runtime.get("ready") or runtime.get("backend") != "direct_nr":
        raise ValueError("This video path requires a ready direct_nr Runtime Configuration")
    if sequence.get("schema_version") != 2:
        raise ValueError("Recreate Prepare DLSS Video with the current node definitions")
    if contract.get("schema_version") != 2 or contract.get("mode") != "native":
        raise ValueError("Use current DLSS History Settings; SR/DLAA quality presets are not applicable")
    for effect in profiles:
        if effect is not None:
            leaf_profiles, _plan = expand_effect(effect)
            for profile in leaf_profiles:
                profile_settings(profile, 64, 64, 1, contract["warmup_frames"])
    return guide_settings(sequence["settings"])


@tracked("preview")
@managed_job
def run_preview(*, sequence, runtime, contract, profile_a, profile_b, start_time, duration, preview_scale,
                node_id, client_id=None, workflow_id="", preview_mode="range", cursor_time=0.0, process_to_end=False,
                retain_prepared_cache=True):
    if type(retain_prepared_cache) is not bool:
        raise ValueError("retain_prepared_cache must be boolean")
    record = preview_sessions.create(sequence=sequence, runtime=runtime, contract=contract,
                                     profile_a=profile_a, profile_b=profile_b, start_time=start_time,
                                     duration=duration, preview_scale=preview_scale, node_id=node_id,
                                     client_id=client_id, workflow_id=workflow_id)
    trace = current_trace()
    trace.preview = record
    from comfy_api.latest import InputImpl
    import folder_paths
    job = Path(folder_paths.get_temp_directory()) / "dlss-experimental" / record.session_id
    job.mkdir(parents=True, exist_ok=False)
    attach_job(job)
    check_job()
    trace.report_file = job / "session.json"
    cancelled = lambda: trace.release_requested.is_set() or record.cancelled.is_set() or interrupted()
    last_event = 0.0

    def publish(**fields):
        payload = preview_sessions.update(record, **fields)
        send_preview_event(payload, client_id)

    def progress(stage, done, total):
        nonlocal last_event
        check_cancel(cancelled)
        now = time.monotonic()
        if now - last_event > 0.2 or done == total:
            publish(state="rendering", progress={"stage": stage, "done": done, "total": total})
            last_event = now

    publish(state="preparing")
    prepared_lease = None
    try:
        with media_tools_scope(sequence.get("media_tools_config")):
            guides = validate_inputs(sequence, runtime, contract, [profile_a, profile_b])
            _profiles_b, plan_b = expand_effect(profile_b)
            plan_a = expand_effect(profile_a)[1] if profile_a is not None else None
            trace.record["nr_effects"] = {"a": plan_a, "b": plan_b}
            input_duration = float(sequence["video"].get_duration())
            duration = resolve_video_range(input_duration, start_time, duration, process_to_end)
            trace.record["resolved_range"] = {"input_duration": input_duration, "start_time": start_time,
                                              "range_duration": duration, "process_to_end": process_to_end}
            publish(input_duration=input_duration, range_duration=duration, process_to_end=process_to_end)
            import math
            if preview_mode not in {"range", "frame"}:
                raise ValueError("Invalid preview mode")
            if type(cursor_time) not in (int, float) or not math.isfinite(cursor_time) or cursor_time < 0 or (preview_mode == "frame" and cursor_time >= duration):
                raise ValueError("Preview cursor must be inside the selected duration")
            selected_start = start_time + (cursor_time if preview_mode == "frame" else 0)
            # Decode actual preceding frames and keep the configured warmup. A frame
            # preview is not a stateless image evaluation nor a quality-equivalent
            # substitute for checking motion in a continuous rendered clip.
            request = ClipRequest(selected_start, 0.1 if preview_mode == "frame" else duration,
                                  contract["pre_roll"], preview_scale / 100, preview_mode == "frame")
            policy = InputColorPolicy.from_dict(sequence.get("color_policy"))
            source, bound = bind_video_source(sequence["video"], request, job, color_policy=policy, cancelled=cancelled)
            trace.record["source_path"] = str(source)
            cache_root = data_root()
            prepared, manifest, reused = prepared_cache(
                source, bound, guides, cache_root, cancelled, progress,
                color_policy=policy, scratch_path=job, lease=True, retain=retain_prepared_cache)
            prepared_lease = (prepared, cache_root)
            publish(preview_mode=preview_mode, cursor_time=cursor_time, selected_start=selected_start)
            trace.record["guide_cache_hit"] = reused
            trace.record["storage_plan"] = manifest.get("storage_plan")
            trace.record["flow_backend"] = manifest.get("flow_backend")
            trace.record["source_sha256"] = manifest["source_sha256"]
            a = (render_variant(prepared, manifest, runtime, profile_a, data_root(), job / "a", contract["warmup_frames"], cancelled, progress)[0]
                 if profile_a is not None else render_original(prepared, manifest, job / "a", cancelled))
            b, report = render_variant(prepared, manifest, runtime, profile_b, data_root(), job / "b", contract["warmup_frames"], cancelled, progress)
            check_cancel(cancelled)
            views = {"original_view": _source_video_view(a), "processed_view": _source_video_view(b),
                     "original_url": None, "processed_url": None, "time_origin": 0}
            if not views["original_view"] or not views["processed_view"]:
                raise RuntimeError("Preview files are outside Comfy's media roots")
            retention = release_prepared_cache(
                prepared, cache_root, retain=retain_prepared_cache, success=True)
            if manifest.get("streaming"):
                retention = {"policy": "streaming_no_prepared_cache", "removed": False, "cache_created": False}
            prepared_lease = None
            trace.record["prepared_cache_retention"] = retention
            report["prepared_cache_retention"] = retention
            publish(state="ready", transport=views, frame_rate=manifest["fps"], frame_count=manifest["visible_count"],
                    duration=manifest["visible_count"] / float(Fraction(manifest["fps"])),
                    source={**record.public["source"], "width": manifest["width"], "height": manifest["height"]},
                    guide_cache_hit=reused, input_report=manifest["metadata"].get("input_report"),
                    guide_mode=guides.motion_provider, guide_settings=asdict(guides), report=report,
                    labels={"a": (f"{plan_a['pass_count']}-pass Stack A" if plan_a and plan_a["pass_count"] > 1 else "Look A")
                            if profile_a else "Adapted original",
                            "b": f"{plan_b['pass_count']}-pass Stack B" if plan_b["pass_count"] > 1 else "Look B"})
            return record.public, InputImpl.VideoFromFile(str(b)), InputImpl.VideoFromFile(str(a))
    except BaseException as exc:
        publish(state="cancelled" if cancelled() else "failed", error=str(exc))
        raise
    finally:
        if prepared_lease is not None:
            prepared, cache_root = prepared_lease
            try:
                trace.record["prepared_cache_retention"] = release_prepared_cache(
                    prepared, cache_root, retain=retain_prepared_cache, success=False)
            except BaseException as cleanup_error:
                trace.record["prepared_cache_retention"] = {
                    "policy": "retain" if retain_prepared_cache else "discard_after_success",
                    "removed": False, "error": f"{type(cleanup_error).__name__}: {cleanup_error}"}
        (job / "session.json").write_text(json.dumps(record.public, indent=2), encoding="utf-8")


@tracked("process")
@with_media_tools
@managed_job
def run_process(*, sequence, runtime, contract, profile, start_time, duration, scale, process_to_end=False,
                retain_prepared_cache=True):
    if type(retain_prepared_cache) is not bool:
        raise ValueError("retain_prepared_cache must be boolean")
    trace = current_trace()
    cancelled = lambda: trace.release_requested.is_set() or interrupted()
    guides = validate_inputs(sequence, runtime, contract, [profile])
    _profiles, effect_plan = expand_effect(profile)
    trace.record["nr_effect"] = effect_plan
    from comfy_api.latest import InputImpl
    import folder_paths
    total = float(sequence["video"].get_duration())
    chosen_duration = resolve_video_range(total, start_time, duration, process_to_end, legacy_zero=True)
    trace.record["resolved_range"] = {"input_duration": total, "start_time": start_time,
                                      "range_duration": chosen_duration, "process_to_end": process_to_end}
    request = ClipRequest(start_time, chosen_duration, contract["pre_roll"], scale / 100)
    request.validate()
    job = Path(folder_paths.get_temp_directory()) / "dlss-experimental" / ("render-" + uuid.uuid4().hex)
    job.mkdir(parents=True, exist_ok=False)
    attach_job(job)
    check_job()
    policy = InputColorPolicy.from_dict(sequence.get("color_policy"))
    source, bound = bind_video_source(sequence["video"], request, job, color_policy=policy, cancelled=cancelled)
    cache_root = data_root()
    prepared, manifest, reused = prepared_cache(
        source, bound, guides, cache_root, cancelled, lambda *_: None,
        color_policy=policy, scratch_path=job, lease=True, retain=retain_prepared_cache)
    prepared_lease = True
    try:
        trace.record["source_path"] = str(source)
        trace.record["source_sha256"] = manifest["source_sha256"]
        trace.record["guide_cache_hit"] = reused
        trace.record["storage_plan"] = manifest.get("storage_plan")
        trace.record["flow_backend"] = manifest.get("flow_backend")
        trace.report_file = job / "result" / "report.json"
        from comfy.utils import ProgressBar
        progress_bar = ProgressBar(len(manifest["frames"]) * effect_plan["pass_count"])

        def progress(_stage, done, count):
            if count:
                progress_bar.update_absolute(done, count)

        output, report = render_variant(prepared, manifest, runtime, profile, data_root(), job / "result",
                                        contract["warmup_frames"], cancelled, progress)
        retention = release_prepared_cache(
            prepared, cache_root, retain=retain_prepared_cache, success=True)
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
        # Persist the same complete report returned by the node, including preparation
        # cache reuse. render_variant writes only the lower-level NR report earlier.
        (job / "result" / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return InputImpl.VideoFromFile(str(output)), report
    finally:
        if prepared_lease:
            try:
                trace.record["prepared_cache_retention"] = release_prepared_cache(
                    prepared, cache_root, retain=retain_prepared_cache, success=False)
            except BaseException as cleanup_error:
                trace.record["prepared_cache_retention"] = {
                    "policy": "retain" if retain_prepared_cache else "discard_after_success",
                    "removed": False, "error": f"{type(cleanup_error).__name__}: {cleanup_error}"}
