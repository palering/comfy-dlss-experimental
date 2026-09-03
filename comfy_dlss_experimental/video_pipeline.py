"""Shared Comfy/CLI orchestration. Graphics DLLs remain in external processes."""
from __future__ import annotations

import contextlib
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

from .direct_nr import DirectNRClient, DirectNRSettings
from .media_clip import ClipRequest, export_video, file_hash, prepare_clip, probe_media
from .native_relay import NativeRelay
from .temporal_guides import GuideSettings
from .flow_provider import FlowProvider
from .input_policy import InputColorPolicy
from .execution_log import measured, phase, current_trace

_GPU_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()


def check_cancel(cancelled):
    if cancelled():
        raise InterruptedError("DLSS processing cancelled")


@contextlib.contextmanager
def cancellable_lock(lock, cancelled):
    while not lock.acquire(timeout=0.1):
        check_cancel(cancelled)
    try:
        check_cancel(cancelled)
        yield
    finally:
        lock.release()


def profile_settings(profile: dict, width: int, height: int, frames: int, warmup: int) -> DirectNRSettings:
    if profile.get("schema_version") != 2:
        raise ValueError("Legacy NR Profile: recreate it using the current node (schema 2).")
    if type(profile.get("nr_enabled", True)) is not bool:
        raise ValueError("nr_enabled must be boolean")
    mix = profile.get("mix", 1.0)
    if type(mix) not in (int, float) or not 0 <= mix <= 1:
        raise ValueError("mix must be between zero and one")
    for name in ("automatic_mask", "ui_correction"):
        if type(profile.get(name, False)) is not bool:
            raise ValueError(f"{name} must be boolean")
    style = profile.get("nr_style", 1)
    if type(style) is not int or style not in (0, 1, 2):
        raise ValueError("nr_style must be an experimental style ID: 0, 1 or 2")
    settings = DirectNRSettings(
        width, height, frames, warmup=warmup,
        profile=profile.get("worker_profile", 1),
        preset=profile.get("nr_preset", 0), style=style,
        auto_mask=int(profile.get("automatic_mask", False)),
        ui_correction=int(profile.get("ui_correction", False)),
        intensity=profile.get("intensity", 0.25),
        local_tone=profile.get("local_tone_strength", 1.0),
        local_structure=profile.get("local_structure_strength", 1.0),
        skin_structure=profile.get("skin_structure_strength", -1.0),
    )
    settings.validate()
    return settings


def guide_settings(settings: dict) -> GuideSettings:
    if settings.get("schema_version") != 2:
        raise ValueError("Legacy Temporal Settings: recreate them with the current DIS guide node.")
    connected = settings.get("flow_provider")
    flow = FlowProvider.from_payload(connected) if connected is not None else None
    result = GuideSettings(settings.get("analysis_scale", 50) / 100,
                           settings.get("scene_cut_threshold", 0.35),
                           settings.get("consistency_tolerance", 2.5),
                           flow.kind if flow else settings.get("motion_provider", "dis"), flow)
    result.validate()
    return result


@measured("input_binding")
def bind_video_source(video, request: ClipRequest, job: Path, *,
                      color_policy: InputColorPolicy = InputColorPolicy(), cancelled=None) -> tuple[Path, ClipRequest]:
    """Respect VIDEO trims/crops. Fast-path only a known file-backed Comfy type."""
    request.validate()
    from comfy_api.latest import InputImpl
    if isinstance(video, InputImpl.VideoFromFile):
        source = video.get_stream_source()
        if isinstance(source, (str, Path)):
            source = Path(source).resolve(strict=True)
            metadata = probe_media(source, color_policy=color_policy, cancelled=cancelled)
            raw = metadata["video"]
            # Public dimensions reveal an effective crop. Never bypass it.
            if tuple(video.get_dimensions()) == (raw["width"], raw["height"]):
                offset, _trim_duration = video.get_active_trim_window()
                available = float(video.get_duration())
                duration = min(request.duration, available - request.start)
                if duration <= 0:
                    raise ValueError("Preview starts beyond the VIDEO's active range")
                # Context must stay inside the upstream trim, not reveal excluded frames.
                context = min(request.pre_roll, request.start)
                return source, ClipRequest(offset + request.start, duration, context, request.scale, request.single_frame)
            if metadata.get("input_report", {}).get("assumptions"):
                raise ValueError("带上游裁剪且缺少色彩标签的 VIDEO 需要先对原文件适配；不能先按未知色彩物化再补标签。")
    # Tensor-backed or cropped VIDEO: materialize only the requested view, not
    # its hidden underlying source. The public API applies all upstream edits.
    context = min(request.pre_roll, request.start)
    selected = video.as_trimmed(start_time=request.start - context, duration=request.duration + context)
    if selected is None:
        raise ValueError("Selected VIDEO range is empty")
    destination = job / "selected-input.mp4"
    selected.save_to(str(destination), crf=0, preset="ultrafast")
    return destination, ClipRequest(context, request.duration, context, request.scale, request.single_frame)


@measured("runtime_snapshot")
def snapshot_runtime(runtime: dict, root: Path) -> tuple[Path, Path]:
    if not runtime.get("ready") or runtime.get("backend") != "direct_nr":
        raise ValueError("Select a ready direct_nr runtime preset")
    if runtime.get("worker_policy") == "persistent":
        from .resident_worker import persistent_supported
        if not persistent_supported(runtime.get("component_hashes", {})):
            raise ValueError("Persistent reset compatibility is not verified for this worker/model pair")
    targets = {"relay": "dlss-native-relay.exe", "worker": "nvngx.dll", "nvngx_dlssnr": "nvngx_dlssnr.dll"}
    components, hashes = runtime["components"], runtime["component_hashes"]
    if set(components) != set(targets):
        raise ValueError("Direct runtime must contain relay, worker, nvngx_dlssnr")
    # Reject stale Comfy runtime descriptors and copy a real snapshot, not
    # hardlinks which would change under an in-place DLL replacement.
    for role in targets:
        if file_hash(Path(components[role])) != hashes[role]:
            raise ValueError(f"Runtime component {role} changed; re-queue Runtime Configuration")
    key = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    destination = root / "runtime-snapshots" / key
    with _CACHE_LOCK:
        if not destination.exists():
            temporary = destination.with_name(key + ".partial-" + uuid.uuid4().hex)
            temporary.mkdir(parents=True)
            for role, filename in targets.items():
                target = temporary / filename
                shutil.copyfile(components[role], target)
                if file_hash(target) != hashes[role]:
                    raise ValueError(f"Runtime {role} changed while copying")
            temporary.rename(destination)
        else:
            for role, filename in targets.items():
                if file_hash(destination / filename) != hashes[role]:
                    raise ValueError("Runtime snapshot is corrupt; choose a fresh data directory")
    return destination / targets["relay"], destination / targets["worker"]


@measured("input_preparation")
def prepared_cache(source: Path, request: ClipRequest, guides: GuideSettings, root: Path, cancelled, progress, *,
                   color_policy: InputColorPolicy = InputColorPolicy(), scratch_path: Path | None = None) -> tuple[Path, dict, bool]:
    backend = None
    if guides.motion_provider == "nvidia":
        from .nvidia_flow import probe_nvidia
        backend = probe_nvidia(guides.flow.device if guides.flow else 0, cancelled=cancelled)
    identity = {"version": 5, "source": str(source), "sha256": file_hash(source), "flow_backend": backend,
                "request": asdict(request), "guides": asdict(guides), "color_policy": asdict(color_policy)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = root / "prepared-clips" / key
    with cancellable_lock(_CACHE_LOCK, cancelled):
        manifest_file = path / "manifest.json"
        if manifest_file.is_file():
            manifest = json.loads(manifest_file.read_text())
            size = manifest["width"] * manifest["height"] * 4 * len(manifest["frames"])
            if all((path / name).stat().st_size == size for name in ("color.rgba", "motion.rg16f")):
                progress("guides_cached", 0, len(manifest["frames"]))
                return path, manifest, True
            raise ValueError("Prepared cache is truncated; use a fresh data directory")
        progress("preparing_guides", 0, 0)
        temporary = path.with_name(key + ".partial-" + uuid.uuid4().hex)
        manifest = prepare_clip(source, temporary, request, guides, cancelled=cancelled, color_policy=color_policy, scratch_path=scratch_path)
        manifest["flow_backend"] = backend
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        check_cancel(cancelled)
        temporary.rename(path)
        return path, manifest, False


def relay_environment(runtime: dict, root: Path) -> tuple[Path | None, dict]:
    from .platform_runtime import validate_execution_host
    host = validate_execution_host(runtime)
    environment = dict(os.environ)
    if host == "linux":
        from .jobs import augment_graphical_session
        environment, _detected = augment_graphical_session(environment)
        if runtime.get("linux_display_backend") == "wayland_native_experimental":
            raise ValueError("Direct NR requires the verified Xwayland path")
        if not environment.get("DISPLAY"):
            raise ValueError("No unambiguous Xwayland display; set COMFY_DLSS_DISPLAY")
        proton = Path(runtime["proton"]["executable"])
        from .discovery import _steam_bases
        steam = next((p for p in _steam_bases(Path.home()) if p.is_dir()), None)
        if steam is None:
            raise ValueError("Steam client path is missing")
        environment.update(STEAM_COMPAT_CLIENT_INSTALL_PATH=str(steam), PROTON_ENABLE_WAYLAND="0",
                           PROTON_USE_WAYLAND="0", WINE_GRAPHICS_DRIVER="x11")
        # ReShade overrides from a parent shell must not leak into this adapter.
        environment["WINEDLLOVERRIDES"] = ""
    elif host == "windows":
        proton = None
    else:
        raise ValueError("Rendering requires Windows or Linux/Proton")
    variables = (("XDG_CACHE_HOME", "cache"), ("TMPDIR", "tmp")) if host == "linux" else (("TEMP", "tmp"), ("TMP", "tmp"))
    for variable, name in variables:
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        environment[variable] = str(directory)
    return proton, environment


def render_variant(prepared: Path, manifest: dict, runtime: dict, profile: dict, root: Path, job: Path,
                   warmup: int, cancelled, progress) -> tuple[Path, dict]:
    import numpy as np
    job.mkdir(parents=True, exist_ok=False)
    settings = profile_settings(profile, manifest["width"], manifest["height"], len(manifest["frames"]), warmup)
    bypass = not profile.get("nr_enabled", True) or profile.get("mix", 1.0) == 0
    report = {"settings": asdict(settings), "profile": profile, "bypassed": bypass, "passed": False,
              "color_pipeline": manifest.get("color_pipeline")}
    plane = settings.plane_bytes
    from .storage_budget import require_disk
    # Raw result plus encoded result allowance, checked before launching a GPU
    # process. No allocation proportional to duration is made in GPU memory.
    require_disk(job, plane * manifest["visible_count"] * 2, stage="NR 结果与编码")
    session = None
    lease = None
    resident_result = None
    frames_complete = False
    stop = threading.Event()
    watcher = None
    output_hash = hashlib.sha256()
    frame_seconds = []
    trace = current_trace()
    from .resident_worker import compatibility_key, resident_worker
    persistent = runtime.get("worker_policy") == "persistent"
    from .resident_worker import STREAM_CAPACITY
    if persistent and settings.frame_count >= STREAM_CAPACITY:
        persistent = False
        report["resident_fallback"] = "long_range_exceeds_resident_stream_capacity"
        if trace:
            trace.record["resident_fallback"] = report["resident_fallback"]
            trace.record["runtime"]["worker_policy"] = "isolated"
    with cancellable_lock(_GPU_LOCK, cancelled):
        try:
            # Switching back to isolated also frees a previously retained worker,
            # including a bypassed NR task. No second worker overlaps its prefix.
            if not persistent or bypass:
                resident_worker.retire_idle("isolated_or_bypass")
            if not bypass:
                key = runtime.get("runtime_key", "")
                if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                    raise ValueError("Invalid runtime fingerprint; re-queue Runtime Configuration")
                relay, worker = snapshot_runtime(runtime, root)
                proton, environment = relay_environment(runtime, root)
                progress("starting_worker", 0, settings.frame_count)
                if trace:
                    trace.set_worker_state("starting")
                def launch():
                    with phase("worker_startup"):
                        return NativeRelay(relay, worker, job / "worker", proton=proton,
                            compatdata=root / "prefixes" / ("direct-" + runtime["runtime_key"][:24]),
                            environment=environment, timeout=1020, worker_timeout=3600)
                if persistent:
                    with phase("worker_acquire"):
                        lease = resident_worker.acquire(key=compatibility_key(runtime, settings, environment),
                            runtime=runtime, settings=settings, factory=launch,
                            idle_seconds=runtime.get("idle_timeout_seconds", 300), trace=trace)
                    session, client = lease.session, lease.client
                else:
                    session = launch()
                    client = DirectNRClient(session, settings)
                    if trace:
                        trace.record["worker_reused"] = False
                if trace:
                    trace.set_worker_state("running")
                def watch_cancel():
                    while not stop.wait(0.1):
                        if cancelled():
                            with contextlib.suppress(OSError, RuntimeError):
                                session.cancel()
                            return
                watcher = threading.Thread(target=watch_cancel, name="dlss-job-cancel", daemon=True)
                watcher.start()
            raw = job / "output.rgba"
            with (prepared / "color.rgba").open("rb") as colors, (prepared / "motion.rg16f").open("rb") as motions, raw.open("xb") as output:
                for index, frame in enumerate(manifest["frames"]):
                    check_cancel(cancelled)
                    if index % 64 == 0:
                        require_disk(job, plane * min(65, len(manifest["frames"]) - index), stage="NR 写入")
                    with phase("cache_read_verify"):
                        color, motion = colors.read(plane), motions.read(plane)
                        if (len(color) != plane or len(motion) != plane
                                or hashlib.sha256(color).hexdigest() != frame["color_sha256"]
                                or hashlib.sha256(motion).hexdigest() != frame["motion_sha256"]):
                            raise ValueError("Prepared media cache failed integrity check")
                    tick = time.perf_counter()
                    first_phase = "history_reset_first_frame" if lease and lease.reused else "first_frame_and_warmup"
                    with phase(first_phase if index == 0 else "nr_frames_and_transport"):
                        rendered = color if bypass else client.process(color, motion, frame["pts_ns"], reset=frame["reset"] or index == 0)
                    frame_seconds.append(time.perf_counter() - tick)
                    if frame["visible"]:
                        mix = profile.get("mix", 1.0)
                        if not bypass and mix < 1:
                            a = np.frombuffer(color, np.uint8).astype(np.float32)
                            b = np.frombuffer(rendered, np.uint8).astype(np.float32)
                            rendered = np.rint(a * (1 - mix) + b * mix).clip(0, 255).astype(np.uint8).tobytes()
                        output.write(rendered)
                        output_hash.update(rendered)
                    progress("rendering", index + 1, settings.frame_count)
            if session:
                if not lease:
                    with phase("worker_finish"):
                        report["exit"] = client.finish()
                else:
                    report["exit"] = None  # frame results verified; process intentionally continues
                report["successful_output_frames"] = len(manifest["frames"])
            frames_complete = True
            # Hash visible RGBA bytes before lossy encoding, for repeatable A/B
            # diagnostics. A changed hash is not evidence of better quality.
            report["raw_output_sha256"] = output_hash.hexdigest()
        except Exception as exc:
            if trace and trace.release_requested.is_set():
                raise InterruptedError("Worker 手动释放：本次任务已终止") from exc
            raise
        finally:
            stop.set()
            if watcher:
                watcher.join(timeout=2)
                if watcher.is_alive():
                    frames_complete = False
            if lease:
                with phase("worker_return"):
                    resident_result = resident_worker.finish(lease, complete=frames_complete and not cancelled())
                report["resident"] = resident_result
            elif session:
                if trace:
                    trace.set_worker_state("releasing")
                try:
                    with phase("worker_cleanup"):
                        session.close()
                except BaseException:
                    if trace:
                        trace.set_worker_state("cleanup_failed")
                    raise
                if trace:
                    trace.set_worker_state("released")
                report["cleanup"] = session.cleanup
            if trace:
                trace.marker = None
            if frame_seconds:
                ordered = sorted(frame_seconds[1:])
                report["performance"] = {"first_frame_seconds": frame_seconds[0],
                    "frame_exchange_seconds": sum(frame_seconds),
                    "steady_frame_median_seconds": ordered[len(ordered) // 2] if ordered else None,
                    "steady_frame_p95_seconds": ordered[min(len(ordered) - 1, int(len(ordered) * .95))] if ordered else None,
                    "note": "Host wall time: includes NR, upload/download, relay/pipe transport; not GPU kernel time."}
            (job / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    destination = job / "output.mp4"
    try:
        check_cancel(cancelled)
        progress("encoding", 0, manifest["visible_count"])
        with phase("encoding_b"):
            export_video(raw, destination, manifest, cancelled=cancelled)
        check_cancel(cancelled)
    except BaseException:
        if resident_result and resident_result["retained"]:
            resident_worker.request_release(resident_result["worker_id"], mode="idle")
        raise
    report["passed"] = True
    (job / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    # Only our exact, completed per-job raw spool is removed. Prepared guides
    # stay reusable; failed-job spools and diagnostics remain available.
    raw.unlink()
    return destination, report


@measured("original_a_export")
def render_original(prepared: Path, manifest: dict, job: Path, cancelled) -> Path:
    job.mkdir(parents=True, exist_ok=False)
    # Look edits do not change A. Cache the *encoded* adapted original as well as
    # its pixels, otherwise every comparison pays another full x264 encode.
    cache = prepared / "original-preview-v1"
    with cancellable_lock(_CACHE_LOCK, cancelled):
        if cache.is_dir():
            identity = json.loads((cache / "identity.json").read_text())
            cached = cache / "original.mp4"
            if cached.stat().st_size != identity["bytes"] or file_hash(cached) != identity["sha256"]:
                raise ValueError("Original preview cache failed integrity check")
            destination = job / "original.mp4"
            from .storage_budget import require_disk
            require_disk(job, identity["bytes"], stage="对照视频复制")
            shutil.copyfile(cached, destination)
            trace = current_trace()
            if trace:
                trace.record["original_cache_hit"] = True
            return destination
        destination = _render_original_uncached(prepared, manifest, job, cancelled)
        check_cancel(cancelled)
        temporary = cache.with_name(cache.name + ".partial-" + uuid.uuid4().hex)
        from .storage_budget import require_disk
        require_disk(prepared, destination.stat().st_size, stage="对照视频缓存")
        temporary.mkdir()
        shutil.copyfile(destination, temporary / "original.mp4")
        (temporary / "identity.json").write_text(json.dumps({"bytes": destination.stat().st_size,
                                                           "sha256": file_hash(destination)}))
        temporary.rename(cache)
        trace = current_trace()
        if trace:
            trace.record["original_cache_hit"] = False
        return destination


def _render_original_uncached(prepared: Path, manifest: dict, job: Path, cancelled) -> Path:
    raw = job / "original.rgba"
    plane = manifest["width"] * manifest["height"] * 4
    from .storage_budget import require_disk
    require_disk(job, plane * manifest["visible_count"] * 2, stage="输入对照导出")
    with (prepared / "color.rgba").open("rb") as source, raw.open("xb") as target:
        source.seek(manifest["visible_start_index"] * plane)
        for _ in range(manifest["visible_count"]):
            check_cancel(cancelled)
            if _ % 64 == 0:
                require_disk(job, plane * min(65, manifest["visible_count"] - _), stage="输入对照写入")
            chunk = source.read(plane)
            if len(chunk) != plane:
                raise ValueError("Truncated original clip")
            target.write(chunk)
    destination = job / "original.mp4"
    export_video(raw, destination, manifest, cancelled=cancelled)
    raw.unlink()
    return destination
