"""Bounded SR/DLAA video executor over CSR1, not a resize fallback.

Source color and motion stream through the existing media adapter. Device depth
is an explicit, content-bound external plane read at the same source timestamp.
Only the encoded output is retained; GPU sessions are isolated and always reaped.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import contextlib
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import hashlib
from itertools import chain
import json
from pathlib import Path
import threading
import uuid

from .execution_log import current_trace, phase, tracked
from .execution_context import with_execution_context
from .external_guides import ExternalGuideProvider, reopen_external_guide
from .input_policy import InputColorPolicy
from .media_clip import ClipRequest, file_hash
from .media_pipeline import require_pipeline
from .media_tools import media_tools_scope
from .owned_adapter import half_to_rgba8, rgba8_to_half
from .owned_process import OwnedWorkerProcess
from .sr_contract import SRFrameMetadata, SRSettings, plan_sr
from .storage_manager import attach_job, check_job, managed_job
from .stream_media import StreamEncoder, inspect_stream, prepared_frames
from .video_pipeline import (bind_video_source, cancellable_lock, check_cancel,
                             guide_settings, relay_environment, _GPU_LOCK)
from .video_source import VideoSource
from .video_range import resolve_video_range


@dataclass(frozen=True)
class SRVideoInput:
    """Execution data, with no Comfy VIDEO or graph ownership in the core.

    Content/view/grid/PTS checks still happen against the actual decoded source.
    A manifest's declaration is not proof that estimated depth is calibrated.
    """
    sequence: dict
    depth: ExternalGuideProvider
    attachment_roles: tuple[str, ...] = ("depth",)

    def __post_init__(self):
        if not isinstance(self.sequence, dict) or "video" in self.sequence:
            raise ValueError("SR execution metadata must not contain a VIDEO object")
        if not isinstance(self.depth, ExternalGuideProvider) or self.depth.role != "depth":
            raise ValueError("SR requires an external numerical device_z depth provider")
        roles = tuple(self.attachment_roles)
        if (any(role not in {"depth", "motion", "normals", "mask", "confidence"} for role in roles)
                or len(set(roles)) != len(roles) or "depth" not in roles):
            raise ValueError("Invalid SR attachment roles")
        object.__setattr__(self, "sequence", deepcopy(self.sequence))
        object.__setattr__(self, "depth", replace(self.depth, source_video=None))
        object.__setattr__(self, "attachment_roles", roles)


def sr_input_from_pipeline(pipeline):
    """Check graph ownership before crossing into content-addressed execution."""
    pipeline = require_pipeline(pipeline)
    if pipeline.stages:
        raise ValueError("SR currently accepts an input-only pipeline; render existing NR/SR stages first and reload their output")
    sequence = pipeline.sequence_copy()
    depth = next((attachment for attachment in pipeline.attachments if attachment.role == "depth"), None)
    if depth is None or not isinstance(depth.payload, ExternalGuideProvider):
        raise ValueError("SR requires an external numerical device_z depth provider; relative depth or a white/RGB visualization is not sufficient")
    if depth.semantics != depth.payload.semantics:
        raise ValueError("SR attachment semantics differ from its numerical depth provider")
    if depth.payload.source_video is not sequence["video"]:
        raise ValueError("SR depth must be bound to this exact VIDEO and its unmodified source view")
    return SRVideoInput({key: value for key, value in sequence.items() if key != "video"},
                        depth.payload, tuple(a.role for a in pipeline.attachments))


def validate_sr_input(input_data, runtime, settings, output_width, output_height):
    """Validate source-neutral declarations before decoding or GPU allocation."""
    if not isinstance(input_data, SRVideoInput):
        raise TypeError("SR execution requires SRVideoInput")
    sequence = deepcopy(input_data.sequence)
    if "video" in sequence:
        raise ValueError("SR execution metadata must not contain a VIDEO object")
    if sequence.get("input_report", {}).get("ready") is False:
        raise ValueError("Video Input Adapter must pass validation before SR rendering")
    settings = SRSettings.from_payload(settings)
    if settings.hdr or not settings.auto_exposure or settings.jitter_policy != "unjittered_video" or settings.motion_jittered:
        raise ValueError("SR video execution currently requires SDR, auto exposure and unjittered_video; external jitter/exposure frames are not implemented")
    if not isinstance(runtime, dict) or not runtime.get("ready") or runtime.get("backend") != "owned_sr":
        raise ValueError("Select a ready owned_sr Runtime Configuration; NR runtimes cannot execute SR")
    if runtime.get("worker_policy", "isolated") != "isolated":
        raise ValueError("SR currently requires isolated Worker mode; persistent SR reuse is not enabled")
    project = runtime.get("compatibility", {}).get("project_id")
    try:
        valid_project = isinstance(project, str) and str(uuid.UUID(project)) == project and uuid.UUID(project).int != 0
    except (ValueError, AttributeError):
        valid_project = False
    if not valid_project:
        raise ValueError("owned_sr requires an explicit canonical NGX project_id UUID in the runtime preset")
    key = runtime.get("runtime_key")
    if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid SR runtime fingerprint")
    sizes = sequence.get("public", {})
    plan = plan_sr(settings, sizes.get("width"), sizes.get("height"), output_width, output_height)
    if output_width % 2 or output_height % 2:
        raise ValueError("SR video output dimensions must be even for the current YUV420 encoder")
    guides = guide_settings(sequence["settings"])
    provider = input_data.depth
    if (provider.role != "depth" or provider.semantics != "device_z"
            or provider.units != "zero_to_one" or provider.channels != 1
            or provider.dtype not in ("float16_le", "float32_le")):
        raise ValueError("SR depth must be a numerical device_z plane in [0,1]; projection conversion is not implicit")
    if provider.view_id != "source":
        raise ValueError("SR depth must be bound to the unmodified source view")
    extent = (sizes["width"], sizes["height"])
    if (provider.source_width, provider.source_height) != extent or (provider.width, provider.height) != extent:
        raise ValueError("SR depth source/grid dimensions must match the input; no implicit depth resampling")
    if json.loads(provider.metadata_json).get("reversed_z") is not settings.depth_inverted:
        raise ValueError("Depth reversed_z does not match the SR depth_inverted setting")
    return sequence, settings, guides, provider, plan


def validate_sr_execution(pipeline, runtime, settings, output_width, output_height):
    """Compatibility validator for callers holding the existing graph object."""
    result = validate_sr_input(sr_input_from_pipeline(pipeline), runtime, settings, output_width, output_height)
    return (pipeline.sequence_copy(), *result[1:])


def _depth_index(provider, pts_ns):
    times = provider._timestamps
    first, after = bisect_left(times, pts_ns - 1000), bisect_right(times, pts_ns + 1000)
    if after - first != 1:
        raise ValueError("SR depth has no unique matching timestamp, including pre-roll; rebuild/resample guides explicitly")
    return first


def _depth_plane(provider, index, source_sha256):
    frame = provider.read_frame(index, source_sha256=source_sha256, view_id="source",
                                pts_ns=provider.files[index].pts_ns)
    if frame.dtype == "float32_le":
        return frame.data
    import numpy as np
    return frame.as_numpy().astype("<f4").tobytes()


def _preflight_manifest(manifest, sequence, provider, settings, output_width, output_height, cancelled):
    # Reopen the manifest so an edited provider cannot retain a stale graph/cache
    # identity. Plane content hashes are checked lazily on each bounded read.
    reopened = reopen_external_guide(provider.snapshot_config())
    if reopened.cache_identity != provider.cache_identity:
        raise ValueError("SR depth manifest changed after graph assembly")
    if (manifest["width"], manifest["height"]) != (provider.width, provider.height):
        raise ValueError("Decoded SR input dimensions do not match device depth; resizing depth is not implicit")
    if (manifest["metadata"]["video"]["width"], manifest["metadata"]["video"]["height"]) != (provider.source_width, provider.source_height):
        raise ValueError("Decoded source view differs from the device-depth source")
    plan_sr(settings, manifest["width"], manifest["height"], output_width, output_height)
    if manifest.get("color_pipeline", {}).get("working_transfer") != "iec61966-2-1":
        raise ValueError("SR SDR input requires sRGB working color; enable normalize_to_srgb in Video Input Adapter for BT.709 input")
    if manifest["source_sha256"] != provider.source_sha256 or file_hash(Path(manifest["source"])) != provider.source_sha256:
        raise ValueError("SR depth source content hash differs from decoded VIDEO; regenerate guides for the actual crop/trim/materialized source")
    frames = manifest["frames"]
    if not 1 <= len(frames) <= 1_000_000 or not 1 <= manifest["visible_count"] <= len(frames):
        raise ValueError("SR frame count exceeds the bounded Worker contract")
    if type(manifest["visible_start_index"]) is not int or not 0 <= manifest["visible_start_index"] < len(frames):
        raise ValueError("Invalid SR visible frame range")
    if manifest["visible_count"] != len(frames) - manifest["visible_start_index"]:
        raise ValueError("SR visible frames must form the trailing selected range")
    indices = []
    last = -1
    root = provider.manifest_path.parent.resolve()
    for index, frame in enumerate(frames):
        check_cancel(cancelled)
        pts = frame["pts_ns"]
        if type(pts) is not int or not 0 <= pts < 1 << 63 or pts <= last:
            raise ValueError("SR frames require increasing source timestamps")
        if frame["visible"] is not (index >= manifest["visible_start_index"]):
            raise ValueError("SR manifest visibility disagrees with the selected range")
        last = pts
        depth_index = _depth_index(provider, pts)
        path = (root / provider.files[depth_index].path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("A requested SR depth frame is missing or escapes its manifest directory")
        indices.append(depth_index)
    return indices


def render_sr_stream(manifest, pipeline, runtime, settings, output_width, output_height,
                     root, job, cancelled=lambda: False, progress=lambda *_: None):
    """Compatibility adapter; the renderer itself consumes source-neutral data."""
    return render_sr_input_stream(manifest, sr_input_from_pipeline(pipeline), runtime, settings,
                                   output_width, output_height, root, job, cancelled, progress)


def render_sr_input_stream(manifest, input_data, runtime, settings, output_width, output_height,
                           root, job, cancelled=lambda: False, progress=lambda *_: None):
    """Evaluate every input/pre-roll frame, encode only the selected output range."""
    sequence, settings, guides, depth, plan = validate_sr_input(
        input_data, runtime, settings, output_width, output_height)
    indices = _preflight_manifest(manifest, sequence, depth, settings, output_width, output_height, cancelled)
    # Imports are dispatch-local: loading NR nodes must not load SR SDK state.
    from .sr_worker import OwnedSRSettings
    from .sr_runtime import snapshot_sr
    native = OwnedSRSettings(manifest["width"], manifest["height"], output_width, output_height, settings=settings)
    native.encode()
    output_manifest = deepcopy(manifest)
    output_manifest["width"], output_manifest["height"] = output_width, output_height
    # Keep input timing/audio/source metadata intact, explicitly identify output.
    output_manifest["source_dimensions"] = {"width": manifest["width"], "height": manifest["height"]}
    output_manifest["output_dimensions"] = {"width": output_width, "height": output_height}
    root, job = Path(root), Path(job)
    job.mkdir(parents=True, exist_ok=False)
    trace = current_trace()
    session = iterator = watcher = None
    stop = threading.Event()
    output_hash, full_hash = hashlib.sha256(), hashlib.sha256()
    processed = encoded = 0
    output_bytes = output_width * output_height * 4
    input_bytes = manifest["width"] * manifest["height"] * 4
    rate = Fraction(manifest["fps"])
    if not 1 <= rate <= 120:
        raise ValueError("SR requires the validated CFR video rate in [1,120]")

    def watch():
        while not stop.wait(.1):
            if cancelled():
                with contextlib.suppress(OSError, RuntimeError):
                    session.cancel()
                return

    with cancellable_lock(_GPU_LOCK, cancelled):
        try:
            # Reclaim an idle NR lease before creating another feature session;
            # no arbitrary Wine/NVIDIA processes are stopped.
            from .resident_worker import resident_worker
            resident_worker.retire_idle("isolated_sr")
            worker, model = snapshot_sr(runtime, root)
            proton, environment = relay_environment(runtime, root)
            # Enter the actual decoder/guide provider before starting SR. Missing
            # PyAV/OpenCV/NVOF helpers fail here, not after allocating SR state.
            iterator = prepared_frames(manifest, guides, cancelled)
            try:
                first_frame = next(iterator)
            except StopIteration as exc:
                raise ValueError("SR input contains no decoded frame") from exc
            first_depth = _depth_plane(depth, indices[0], manifest["source_sha256"])
            check_cancel(cancelled)
            if trace:
                trace.set_worker_state("starting")
            with phase("worker_startup"):
                session = OwnedWorkerProcess(worker, model.parent, None, job / "worker", feature="sr",
                    project_id=runtime["compatibility"]["project_id"], proton=proton,
                    compatdata=root / "prefixes" / ("owned-sr-" + runtime["runtime_key"][:24]) if proton else None,
                    environment=environment, timeout=120)
            watcher = threading.Thread(target=watch, daemon=True, name="dlss-sr-cancel")
            watcher.start()
            if trace:
                trace.marker = session.run_marker
                trace.record.update(worker_instances=1, effective_worker_policy="isolated", worker_reused=False)
                trace.set_worker_state("running")
            with phase("worker_session_create"):
                session.client.create(native, session_id=1)
            with StreamEncoder(job / "output.mp4", output_manifest, cancelled) as encoder:
                for index, (color, motion, frame) in enumerate(chain((first_frame,), iterator)):
                    check_cancel(cancelled)
                    if index >= len(manifest["frames"]) or any(frame[k] != manifest["frames"][index][k] for k in ("pts_ns", "visible")):
                        raise ValueError("SR input timing changed during rendering")
                    if len(color) != input_bytes or len(motion) != input_bytes:
                        raise ValueError("SR color/motion plane does not match the input dimensions")
                    with phase("sr_depth_input"):
                        depth_data = first_depth if index == 0 else _depth_plane(depth, indices[index], manifest["source_sha256"])
                    reset = bool(index == 0 or frame["reset"] or depth.files[indices[index]].reset)
                    metadata = SRFrameMetadata(frame_time_ms=float(1000 / rate), reset=reset)
                    with phase("owned_color_conversion"):
                        color_half = rgba8_to_half(color)
                    with phase("sr_frames_and_transport"):
                        half = session.client.process(color_half, motion, depth_data, frame["pts_ns"], metadata=metadata)
                    with phase("owned_color_conversion"):
                        rendered = half_to_rgba8(half, output_bytes)
                    full_hash.update(rendered)
                    processed += 1
                    if frame["visible"]:
                        with phase("encoding_output"):
                            encoder.write(rendered)
                        output_hash.update(rendered)
                        encoded += 1
                    progress("streaming_sr", processed, len(manifest["frames"]))
                    if index % 32 == 0:
                        check_job()
                if processed != len(manifest["frames"]) or encoded != manifest["visible_count"]:
                    raise ValueError("SR did not evaluate every input/pre-roll frame")
                check_cancel(cancelled)
                with phase("worker_finish"):
                    session.client.end()
                    session.shutdown()
                with phase("encoding_output"):
                    destination = encoder.finish()
            check_cancel(cancelled)
        except BaseException as exc:
            if cancelled() and not isinstance(exc, InterruptedError):
                raise InterruptedError("SR rendering cancelled") from exc
            raise
        finally:
            stop.set()
            if watcher is not None:
                watcher.join(timeout=2)
            errors = []
            if iterator is not None and hasattr(iterator, "close"):
                try:
                    iterator.close()
                except BaseException as exc:
                    errors.append(str(exc))
            if session is not None:
                if trace:
                    trace.set_worker_state("releasing")
                try:
                    with phase("worker_cleanup"):
                        session.close()
                except BaseException as exc:
                    errors.append(str(exc))
            if trace:
                trace.marker = None
                trace.set_worker_state("cleanup_failed" if errors else "released")
            if errors:
                raise RuntimeError("SR Worker cleanup failed: " + "; ".join(errors))
    report = {"passed": True, "schema_version": 1, "feature": plan["feature"],
        "backend_id": "owned_csr1_sr", "protocol": "CSR1", "settings": settings.to_payload(),
        "input_dimensions": {"width": manifest["width"], "height": manifest["height"]},
        "output_dimensions": {"width": output_width, "height": output_height},
        "source_metadata": manifest["metadata"], "frame_count": encoded, "evaluated_frames": processed,
        "pre_roll_frames": processed - encoded, "fps": manifest["fps"],
        "raw_output_sha256": output_hash.hexdigest(), "all_frames_sha256": full_hash.hexdigest(),
        "depth_provider": depth.report(), "guide_mode": guides.motion_provider, "guide_settings": asdict(guides),
        "unconsumed_attachments": [role for role in input_data.attachment_roles if role != "depth" and
                                   not (role == "motion" and guides.motion_provider == "external")],
        "color_pipeline": manifest["color_pipeline"], "storage_mode": "streaming", "raw_disk_bytes": 0,
        "guide_cache_hit": False, "worker_instances": 1, "effective_worker_policy": "isolated",
        "cleanup": session.cleanup, "output_path": str(destination),
        "execution_contract": {"backend_id": "owned_csr1_sr", "protocol": "CSR1",
            "worker_color": "rgba16f_le", "motion": "rg16f_le", "depth": "r32f_le",
            "input_transfer": "srgb", "implicit_eotf": False, "float_output_quantized": True,
            "synthetic_jitter": False, "exposure": "ngx_auto_exposure", "nr_warmup_applied": False},
        "notes": ["SR engine evaluated every frame; no resize fallback is used.",
                  "Zero jitter and image-estimated motion do not recreate independently jittered engine samples.",
                  "Depth declarations are not proof of calibrated engine geometry; frame hashes, range and PTS are checked."]}
    (job / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return destination, report


def run_sr_process(*, pipeline, runtime, settings, output_width, output_height, start_time=0,
                   duration=0, process_to_end=True, pre_roll=.5):
    """Stable Comfy endpoint; the same SR executor can also accept a file source."""
    from .comfy_adapter import ComfyVideoSource, comfy_execution_context
    from comfy_api.latest import InputImpl

    input_data = sr_input_from_pipeline(pipeline)
    output, report = render_sr_video(source=ComfyVideoSource(pipeline.sequence_copy()["video"]),
        context=comfy_execution_context(), input_data=input_data, runtime=runtime, settings=settings,
        output_width=output_width, output_height=output_height, start_time=start_time, duration=duration,
        process_to_end=process_to_end, pre_roll=pre_roll)
    return InputImpl.VideoFromFile(str(output)), report


@with_execution_context
@tracked("process")
@managed_job
def render_sr_video(*, source, context, input_data, runtime, settings, output_width, output_height,
                    start_time=0, duration=0, process_to_end=True, pre_roll=.5):
    """Host-neutral SR file task, with explicit paths/progress/cancellation."""
    if not isinstance(source, VideoSource):
        raise TypeError("SR file execution requires a VideoSource adapter")
    sequence, settings, guides, _depth, _plan = validate_sr_input(
        input_data, runtime, settings, output_width, output_height)
    trace = current_trace()
    cancelled = lambda: trace.release_requested.is_set() or context.cancelled()
    check_cancel(cancelled)
    job = context.temp_root / "dlss-experimental" / ("render-" + uuid.uuid4().hex)
    job.mkdir(parents=True, exist_ok=False)
    attach_job(job)
    check_job()
    trace.record.update(feature="sr", input=sequence.get("public", {}), guides=sequence["settings"],
                        sr_settings=settings.to_payload(), color_policy=sequence.get("color_policy"))
    with media_tools_scope(sequence.get("media_tools_config")):
        # Fail missing media modules before a native process is created, including
        # cv2 used for motion/cut analysis even in explicit-zero motion mode.
        _require_media_dependencies()
        policy = InputColorPolicy.from_dict(sequence.get("color_policy"))
        total = source.duration(color_policy=policy, cancelled=cancelled)
        chosen_duration = resolve_video_range(total, start_time, duration, process_to_end, legacy_zero=True)
        request = ClipRequest(start_time, chosen_duration, pre_roll, 1.0)
        request.validate()
        trace.record["resolved_range"] = {"input_duration": total, "start_time": start_time,
                                          "range_duration": chosen_duration, "process_to_end": process_to_end}
        source_path, bound = bind_video_source(source, request, job, color_policy=policy, cancelled=cancelled)
        manifest = inspect_stream(source_path, bound, guides, policy, cancelled, lambda *_: None)
        trace.record.update(source_path=str(source_path), source_sha256=manifest["source_sha256"],
                            storage_plan=manifest["storage_plan"], guide_cache_hit=False)
        trace.report_file = job / "result" / "report.json"
        context.progress("rendering_sr", 0, len(manifest["frames"]))
        output, report = render_sr_input_stream(manifest, input_data, runtime, settings, output_width, output_height,
            context.data_root, job / "result", cancelled, context.progress)
        check_cancel(cancelled)
        check_job()
        return output, report


def _require_media_dependencies():
    import av  # noqa: F401
    import cv2  # noqa: F401
    import numpy  # noqa: F401
