"""Host-neutral, bounded renderer-bundle -> CXR1 -> encoded SDR VIDEO file."""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, replace
from fractions import Fraction
import hashlib
from itertools import chain
import json
import math
from pathlib import Path
import threading
import uuid

from .execution_context import with_execution_context
from .execution_log import current_trace, phase, tracked
from .media_clip import run_cancellable
from .media_tools import media_executable, media_tools_scope
from .owned_process import OwnedWorkerProcess
from .sl_bundle import ReconstructionBundle, _integer, _path
from .sl_runtime import snapshot_sl, validate_sl_runtime
from .storage_manager import attach_job, check_job, managed_job
from .stream_media import StreamEncoder
from .video_pipeline import _GPU_LOCK, cancellable_lock, check_cancel, relay_environment


def output_rgba8(data, width, height, transfer):
    """Explicit SDR export only: finite-check, clamp, OETF if linear, quantize."""
    import numpy as np
    if len(data) != width * height * 8 or transfer not in ("srgb", "linear_sdr"):
        raise ValueError("Reconstruction output has an invalid RGBA16F size or transfer")
    values = np.frombuffer(data, "<f2").astype(np.float32).reshape(-1, 4)
    if not np.isfinite(values).all():
        raise ValueError("Reconstruction output contains non-finite values")
    clipped = int(np.count_nonzero((values[:, :3] < 0) | (values[:, :3] > 1)))
    np.clip(values, 0, 1, out=values)
    if transfer == "linear_sdr":
        rgb = values[:, :3]
        values[:, :3] = np.where(rgb <= .0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - .055)
    return np.rint(values * 255).astype(np.uint8).tobytes(), clipped


def _audio_source(bundle, end_frame, cancelled):
    if bundle.audio_source is None:
        return None
    path = _path(bundle.manifest_path.parent, bundle.audio_source.path)
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1024 * 1024):
            check_cancel(cancelled)
            digest.update(block)
    if digest.hexdigest() != bundle.audio_source.sha256:
        raise ValueError("Reconstruction audio source SHA-256 mismatch")
    probe = run_cancellable([media_executable("ffprobe"), "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], timeout=30, cancelled=cancelled)
    info = json.loads(probe.stdout)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise ValueError("Declared bundle audio source has no audio stream")
    duration = float(audio.get("duration", info.get("format", {}).get("duration", "nan")))
    origin = float(audio.get("start_time", 0))
    if not math.isfinite(duration) or not math.isfinite(origin) or abs(origin) > .05 or duration + .05 < float(end_frame / Fraction(bundle.fps)):
        raise ValueError("Bundle audio must start at timeline zero and cover the selected frame range")
    return path


def _output_manifest(bundle, settings, first, end, audio):
    return {"width": settings.output_width, "height": settings.output_height, "fps": bundle.fps,
        "frames": [{"pts_ns": frame.pts_ns, "visible": True} for frame in bundle.frames[first:end]],
        "visible_start_index": 0, "visible_count": end - first, "source": str(audio) if audio else "",
        "metadata": {"has_audio": audio is not None, "video": {"color_transfer": "iec61966-2-1"}},
        "color_pipeline": {"version": 1, "operation": "already_srgb", "enabled": True,
                           "working_transfer": "iec61966-2-1", "output_transfer": "iec61966-2-1"}}


@dataclass(frozen=True)
class ReconstructionFrame:
    pts_ns: int
    color: bytes
    motion: bytes
    depth: bytes
    camera: object
    metadata: object
    rr: object = None


def reconstruction_job(context):
    job = context.temp_root / "dlss-experimental" / ("reconstruction-" + uuid.uuid4().hex)
    job.mkdir(parents=True, exist_ok=False)
    attach_job(job)
    check_job()
    current_trace().report_file = job / "report.json"
    return job


@with_execution_context
@tracked("process")
@managed_job
def render_reconstruction(*, context, bundle, runtime, feature="sr", mode="quality",
                          output_width=960, output_height=540, start_frame=0, frame_count=0,
                          history_frames=8, include_audio=True, media_tools_config=None):
    """Exact renderer-bundle ranges, sharing the CXR1 executor with VIDEO input."""
    validate_sl_runtime(runtime)
    if not isinstance(bundle, ReconstructionBundle):
        raise TypeError("Connect a Reconstruction Bundle Input")
    trace = current_trace()
    cancelled = lambda: context.cancelled() or trace.release_requested.is_set()
    bundle = bundle.reopen(cancelled)
    native = bundle.settings(feature, mode, output_width, output_height)
    native.encode()
    start = _integer(start_frame, "start frame", 0, len(bundle.frames) - 1)
    count = _integer(frame_count, "frame count", 0, len(bundle.frames))
    history = _integer(history_frames, "history frames", 0, 120)
    if type(include_audio) is not bool:
        raise ValueError("include_audio must be boolean")
    end = len(bundle.frames) if count == 0 else start + count
    if end > len(bundle.frames):
        raise ValueError("Requested reconstruction range exceeds the bundle; no silent truncation")
    first = max(0, start - history)
    check_cancel(cancelled)
    with media_tools_scope(media_tools_config):
        import numpy  # noqa: F401
        audio = _audio_source(bundle, end, cancelled) if include_audio else None
        manifest = _output_manifest(bundle, native, start, end, audio)
        manifest["frames"] = [{"pts_ns": frame.pts_ns, "visible": i >= start}
                              for i, frame in enumerate(bundle.frames[first:end], first)]
        manifest["visible_start_index"] = start - first

        def frames():
            for index in range(first, end):
                color, motion, depth, frame, rr = bundle.read_frame(index, native, cancelled)
                yield ReconstructionFrame(frame.pts_ns, color, motion, depth, frame.camera, frame.metadata, rr)

        return render_reconstruction_frames(context=context, runtime=runtime, native=native, manifest=manifest,
            frames=frames(), job=reconstruction_job(context), cancelled=cancelled,
            input_report={"bundle": bundle.report()},
            range_report={"start_frame": start, "end_frame": end, "history_start": first},
            unconsumed_rr_planes=bundle.has_rr and feature != "rr")


def render_reconstruction_frames(*, context, runtime, native, manifest, frames, job, cancelled,
                                 input_report, range_report, unconsumed_rr_planes=False):
    """Shared bounded CXR1/encoder lifecycle. Called inside an execution context.

    Sources own decoding, camera/plane identity and optional derivation. This
    executor consumes the same frame contract for renderer bundles and VIDEO.
    It does not dispatch CSR1, infer inputs, or silently interpolate/resize.
    """
    validate_sl_runtime(runtime)
    native.encode()
    trace = current_trace()
    expected = manifest["frames"]
    visible_start = manifest["visible_start_index"]
    if (not expected or not 0 <= visible_start < len(expected)
            or manifest["visible_count"] != len(expected) - visible_start
            or any(item["visible"] is not (i >= visible_start) for i, item in enumerate(expected))):
        raise ValueError("Invalid reconstruction output visibility/range")
    trace.record.update(**input_report, feature=native.feature, reconstruction_settings=native.to_payload(),
                        resolved_range=range_report, worker_instances=0, effective_worker_policy="isolated")
    owner = watcher = None
    stop = threading.Event()
    raw_hash, export_hash = hashlib.sha256(), hashlib.sha256()
    encoded = processed = clipped = 0

    def watch():
        while not stop.wait(.1):
            if cancelled():
                with contextlib.suppress(OSError, RuntimeError):
                    owner.cancel()
                return

    with contextlib.closing(iter(frames)) as iterator:
        try:
            # Validate/read the first complete packet before GPU allocation.
            first_frame = next(iterator)
        except StopIteration as error:
            raise ValueError("No reconstruction input frames") from error
        if not isinstance(first_frame, ReconstructionFrame) or first_frame.pts_ns != expected[0]["pts_ns"]:
            raise ValueError("Reconstruction first packet differs from the validated source timeline")
        with cancellable_lock(_GPU_LOCK, cancelled):
            try:
                from .resident_worker import resident_worker
                resident_worker.retire_idle("isolated_sl")
                with phase("runtime_snapshot"):
                    worker, runtime_dir = snapshot_sl(runtime, context.data_root)
                    proton, environment = relay_environment(runtime, context.data_root)
                check_cancel(cancelled)
                trace.set_worker_state("starting")
                with phase("worker_startup"):
                    owner = OwnedWorkerProcess(worker, runtime_dir, None, job / "worker", feature="sl",
                        project_id=runtime["compatibility"]["project_id"], proton=proton,
                        compatdata=context.data_root / "prefixes" / ("owned-sl-" + runtime["runtime_key"][:24]) if proton else None,
                        environment=environment, timeout=120)
                trace.marker = owner.run_marker
                trace.record.update(worker_instances=1, worker_reused=False)
                trace.set_worker_state("running")
                watcher = threading.Thread(target=watch, name="dlss-sl-cancel", daemon=True)
                watcher.start()
                with phase("worker_session_create"):
                    owner.client.create(native, session_id=1)
                with StreamEncoder(job / "output.mp4", manifest, cancelled) as encoder:
                    for index, frame in enumerate(chain((first_frame,), iterator)):
                        first_frame = None
                        check_cancel(cancelled)
                        if (index >= len(expected) or not isinstance(frame, ReconstructionFrame)
                                or frame.pts_ns != expected[index]["pts_ns"]):
                            raise ValueError("Reconstruction frame count or source PTS changed after input scan")
                        metadata = replace(frame.metadata, reset=True) if index == 0 else frame.metadata
                        with phase("reconstruction_frames_and_transport"):
                            half = owner.client.process(frame.color, frame.motion, frame.depth, frame.pts_ns,
                                camera=frame.camera, metadata=metadata, rr=frame.rr)
                        processed += 1
                        if index >= visible_start:
                            with phase("reconstruction_sdr_export"):
                                rgba, clipping = output_rgba8(half, native.output_width, native.output_height, native.color_transfer)
                            raw_hash.update(half)
                            export_hash.update(rgba)
                            clipped += clipping
                            with phase("encoding_output"):
                                encoder.write(rgba)
                            encoded += 1
                        context.progress("reconstruction", processed, len(expected))
                        check_job()
                    if encoded != manifest["visible_count"] or processed != len(expected):
                        raise ValueError("Reconstruction did not deliver the complete requested frame range")
                    check_cancel(cancelled)
                    with phase("worker_finish"):
                        owner.client.end()
                        owner.shutdown()
                    with phase("encoding_output"):
                        destination = encoder.finish()
                check_cancel(cancelled)
            except BaseException as error:
                if cancelled() and not isinstance(error, InterruptedError):
                    raise InterruptedError("Reconstruction cancelled") from error
                raise
            finally:
                stop.set()
                if watcher is not None:
                    watcher.join(timeout=2)
                if owner is not None:
                    trace.set_worker_state("releasing")
                    try:
                        owner.close()
                    except BaseException:
                        trace.set_worker_state("cleanup_failed")
                        raise
                    finally:
                        trace.marker = None
                trace.set_worker_state("released")
    report = {"schema_version": 1, "passed": True, "backend_id": "owned_sl", "protocol": "CXR1",
        "feature": "rr_dlaa" if native.feature == "rr" and native.mode == "dlaa" else "dlaa" if native.mode == "dlaa" else native.feature,
        "settings": native.to_payload(), **input_report, "runtime_key": runtime["runtime_key"],
        "frame_count": encoded, "evaluated_frames": processed, "history_frames": processed - encoded,
        **range_report, "first_source_pts_ns": expected[visible_start]["pts_ns"],
        "fps": manifest["fps"], "output_path": str(destination), "has_audio": manifest["metadata"]["has_audio"],
        "raw_rgba16f_sha256": raw_hash.hexdigest(), "export_rgba8_sha256": export_hash.hexdigest(),
        "color_pipeline": {"source_transfer": native.color_transfer, "output_transfer": "srgb",
            "linear_to_srgb": native.color_transfer == "linear_sdr", "clipped_rgb_components": clipped,
            "quantized": "RGBA16F to RGBA8 then YUV420 H264", "hdr": False, "alpha_preserved": False},
        "storage_mode": "streaming", "raw_disk_bytes": 0, "worker_instances": 1,
        "effective_worker_policy": "isolated", "cleanup": owner.cleanup,
        "unconsumed_rr_planes": unconsumed_rr_planes, "foreground_required": False,
        "notes": ["Camera and numerical guides are explicit; any external estimates retain their provenance.",
                  "Output is re-based to zero; CFR interval and aligned optional audio are preserved.",
                  "No resize/interpolation fallback; this is not FG or a natural-video quality guarantee."]}
    trace.report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination, report
