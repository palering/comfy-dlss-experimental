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
from .media_tools import media_tool_identity
from .processing_plan import resolve_nr_parts
from .owned_adapter import OwnedMediaClient, launch_owned, media_contract, owned_settings

_GPU_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_PREPARED_LEASES: dict[Path, dict[str, int | bool]] = {}


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


def _lease_prepared_cache(path: Path, *, retain: bool) -> None:
    """Register a consumer while `_CACHE_LOCK` is held.

    A retained consumer wins over a discard request from a concurrent branch.
    Failed consumers also retain the entry so a retry does not repeat input
    preparation merely because the downstream Worker failed.
    """
    if type(retain) is not bool:
        raise ValueError("retain_prepared_cache must be boolean")
    path = path.resolve()
    state = _PREPARED_LEASES.setdefault(
        path, {"users": 0, "keep_seen": False, "discard_seen": False}
    )
    state["users"] = int(state["users"]) + 1
    state["keep_seen"] = bool(state["keep_seen"]) or retain


def release_prepared_cache(path: Path, root: Path, *, retain: bool, success: bool) -> dict:
    """Release one exact prepared entry and optionally delete it after success.

    Only a hash-named direct child of this data root's `prepared-clips` may be
    removed.  Concurrent users are counted; any concurrent keep request or
    failed consumer preserves the entry.
    """
    if type(retain) is not bool or type(success) is not bool:
        raise ValueError("prepared cache release flags must be boolean")
    raw_path = Path(path)
    expected_parent = (Path(root) / "prepared-clips").resolve()
    if (raw_path.parent.resolve() != expected_parent or len(raw_path.name) != 64 or
            any(c not in "0123456789abcdef" for c in raw_path.name)):
        raise ValueError("Refusing to release an invalid prepared cache path")
    if raw_path.is_symlink():
        raise ValueError("Refusing to remove a symlinked prepared cache")
    path = raw_path.resolve()
    if path.parent != expected_parent or path.name != raw_path.name:
        raise ValueError("Refusing to release an invalid prepared cache path")
    with _CACHE_LOCK:
        state = _PREPARED_LEASES.get(path)
        if state is None or int(state["users"]) < 1:
            raise RuntimeError("Prepared cache lease is missing")
        state["users"] = int(state["users"]) - 1
        if success and not retain:
            state["discard_seen"] = True
        else:
            state["keep_seen"] = True
        if int(state["users"]):
            return {"policy": "retain" if retain else "discard_after_success",
                    "removed": False, "deferred": True,
                    "reason": "other consumers are still using this prepared cache"}
        _PREPARED_LEASES.pop(path)
        discard = bool(state["discard_seen"]) and not bool(state["keep_seen"])
        if not discard:
            reason = ("task did not complete successfully" if not success else
                      "a concurrent consumer requested retention" if state["discard_seen"] else
                      "retention enabled")
            return {"policy": "retain" if retain else "discard_after_success",
                    "removed": False, "deferred": False, "reason": reason}
        if not path.exists():
            return {"policy": "discard_after_success", "removed": False,
                    "deferred": False, "reason": "cache was already absent"}
        try:
            shutil.rmtree(path)
        except OSError as error:
            return {"policy": "discard_after_success", "removed": False,
                    "deferred": False, "reason": "cache cleanup failed",
                    "error": f"{type(error).__name__}: {error}"}
        return {"policy": "discard_after_success", "removed": True,
                "deferred": False, "reason": "successful task requested discard"}


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
                           flow.kind if flow else settings.get("motion_provider", "dis"), flow,
                           settings.get("external_motion"))
    result.validate()
    return result


@measured("input_binding")
def bind_video_source(video, request: ClipRequest, job: Path, *,
                      color_policy: InputColorPolicy = InputColorPolicy(), cancelled=None) -> tuple[Path, ClipRequest]:
    """Compatibility entry: neutral sources bind directly; old VIDEO stays adapted."""
    from .video_source import VideoSource
    if isinstance(video, VideoSource):
        return video.bind(request, job, color_policy=color_policy, cancelled=cancelled or (lambda: False))
    from .comfy_adapter import bind_comfy_video
    return bind_comfy_video(video, request, job, color_policy=color_policy, cancelled=cancelled, probe=probe_media)


@measured("runtime_snapshot")
def snapshot_runtime(runtime: dict, root: Path) -> tuple[Path, Path]:
    if not runtime.get("ready") or runtime.get("backend") not in {"direct_nr", "owned_nr"}:
        raise ValueError("Select a ready direct_nr or owned_nr runtime preset")
    owned = runtime["backend"] == "owned_nr"
    if not owned and runtime.get("worker_policy") == "persistent":
        from .resident_worker import persistent_supported
        if not persistent_supported(runtime.get("component_hashes", {})):
            raise ValueError("Persistent reset compatibility is not verified for this worker/model pair")
    targets = {"relay": "dlss-native-relay.exe", "worker": "nvngx.dll", "nvngx_dlssnr": "nvngx_dlssnr.dll"}
    if owned:
        targets = {"caller": "caller/nvngx.dll", "worker": "comfy-dlss-worker.exe", "nvngx_dlssnr": "nvngx_dlssnr.dll"}
    components, hashes = runtime["components"], runtime["component_hashes"]
    if set(components) != set(targets):
        raise ValueError(f"{runtime['backend']} runtime requires exactly {', '.join(targets)}")
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
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(components[role], target)
                if file_hash(target) != hashes[role]:
                    raise ValueError(f"Runtime {role} changed while copying")
            temporary.rename(destination)
        else:
            for role, filename in targets.items():
                if file_hash(destination / filename) != hashes[role]:
                    raise ValueError("Runtime snapshot is corrupt; choose a fresh data directory")
    return destination / targets["caller" if owned else "relay"], destination / targets["worker"]


@measured("input_preparation")
def prepared_cache(source: Path, request: ClipRequest, guides: GuideSettings, root: Path, cancelled, progress, *,
                   color_policy: InputColorPolicy = InputColorPolicy(), scratch_path: Path | None = None,
                   lease: bool = False, retain: bool = True) -> tuple[Path, dict, bool]:
    backend = None
    if guides.motion_provider == "external":
        from .external_guides import reopen_external_guide
        # Even a cache hit must reject a changed manifest. The cached numerical
        # planes are a content-addressed snapshot; source files are not re-read.
        external = reopen_external_guide(guides.external_motion)
        external.validate_nr_motion()
        backend = external.report()
    if guides.motion_provider == "nvidia":
        from .nvidia_flow import probe_nvidia
        backend = probe_nvidia(guides.flow.device if guides.flow else 0, cancelled=cancelled)
    identity = {"version": 6, "source": str(source), "sha256": file_hash(source), "flow_backend": backend,
                "media_tools": media_tool_identity(),
                "request": asdict(request), "guides": asdict(guides), "color_policy": asdict(color_policy)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = root / "prepared-clips" / key
    from .storage_manager import settings as storage_settings, make_cache_room, MiB
    policy = storage_settings(root)
    meta = probe_media(source, cancelled=cancelled, color_policy=color_policy)
    if guides.motion_provider == "external" and (
            external.source_sha256 != identity["sha256"] or
            (external.source_width, external.source_height) != (meta["video"]["width"], meta["video"]["height"])):
        raise ValueError("External guide source hash/dimensions differ from the VIDEO being prepared")
    width, height = (int(meta["video"][k] * request.scale) // 2 * 2 for k in ("width", "height"))
    from .storage_budget import frame_budget
    from fractions import Fraction
    frames = frame_budget(request.duration, request.pre_roll, Fraction(meta["fps"]), request.single_frame)
    estimated = width * height * 8 * frames
    if estimated > policy["entry_mib"] * MiB:
        from .stream_media import inspect_stream
        manifest = inspect_stream(source, request, guides, color_policy, cancelled, progress)
        manifest["flow_backend"] = backend
        with cancellable_lock(_CACHE_LOCK, cancelled):
            # Opportunistically bound old caches even when the new task streams.
            make_cache_room(root, 0, _PREPARED_LEASES)
            if lease:
                _lease_prepared_cache(path, retain=retain)
        return path, manifest, False
    with cancellable_lock(_CACHE_LOCK, cancelled):
        manifest_file = path / "manifest.json"
        if manifest_file.is_file():
            manifest = json.loads(manifest_file.read_text())
            size = manifest["width"] * manifest["height"] * 4 * len(manifest["frames"])
            if all((path / name).stat().st_size == size for name in ("color.rgba", "motion.rg16f")):
                progress("guides_cached", 0, len(manifest["frames"]))
                make_cache_room(root, 0, [path, *_PREPARED_LEASES])
                if lease:
                    _lease_prepared_cache(path, retain=retain)
                os.utime(path, None)
                return path, manifest, True
            raise ValueError("Prepared cache is truncated; use a fresh data directory")
        progress("preparing_guides", 0, 0)
        # Include room for the encoded original and small manifest files.
        make_cache_room(root, estimated * 2 + MiB, _PREPARED_LEASES)
        temporary = path.with_name(key + ".partial-" + uuid.uuid4().hex)
        try:
            manifest = prepare_clip(source, temporary, request, guides, cancelled=cancelled, color_policy=color_policy, scratch_path=scratch_path)
            manifest["flow_backend"] = backend
            (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            check_cancel(cancelled)
            temporary.rename(path)
        except BaseException:
            if temporary.is_dir() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise
        if lease:
            _lease_prepared_cache(path, retain=retain)
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


def _render_variant_frames(prepared: Path, manifest: dict, runtime: dict, profile: dict, root: Path, job: Path,
                           warmup: int, cancelled, progress, *, color_source: Path,
                           include_history: bool, verify_original: bool) -> tuple[Path, dict]:
    job.mkdir(parents=True, exist_ok=False)
    settings = profile_settings(profile, manifest["width"], manifest["height"], len(manifest["frames"]), warmup)
    owned = runtime.get("backend") == "owned_nr"
    if owned:
        owned_settings(settings)  # Validate before snapshot, process launch or GPU use.
    client_factory = OwnedMediaClient if owned else DirectNRClient
    bypass = not profile.get("nr_enabled", True) or profile.get("mix", 1.0) == 0
    report = {"settings": asdict(settings), "profile": profile, "bypassed": bypass, "passed": False,
              "color_pipeline": manifest.get("color_pipeline"), "execution_contract": media_contract(runtime)}
    plane = settings.plane_bytes
    from .storage_budget import require_disk
    output_count = len(manifest["frames"]) if include_history else manifest["visible_count"]
    expected_input_bytes = plane * len(manifest["frames"])
    if not color_source.is_file() or color_source.stat().st_size != expected_input_bytes:
        raise ValueError("NR pass input has an unexpected RGBA frame count")
    require_disk(job, plane * output_count, stage="NR 中间结果" if include_history else "NR 结果")
    session = None
    lease = None
    resident_result = None
    frames_complete = False
    stop = threading.Event()
    watcher = None
    output_hash = hashlib.sha256()
    all_output_hash = hashlib.sha256()
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
                        if owned:
                            return launch_owned(runtime, relay, worker, job / "worker", root, proton, environment)
                        return NativeRelay(relay, worker, job / "worker", proton=proton,
                            compatdata=root / "prefixes" / ("direct-" + runtime["runtime_key"][:24]),
                            environment=environment, timeout=1020, worker_timeout=3600)
                if persistent:
                    with phase("worker_acquire"):
                        lease = resident_worker.acquire(key=compatibility_key(runtime, settings, environment, owner_root=root),
                            runtime=runtime, settings=settings, factory=launch,
                            idle_seconds=runtime.get("idle_timeout_seconds", 300), trace=trace,
                            client_factory=client_factory)
                    session, client = lease.session, lease.client
                else:
                    session = launch()
                    client = client_factory(session, settings)
                    if trace:
                        trace.record["worker_reused"] = False
                if trace:
                    trace.marker = session.run_marker
                    trace.record["execution_contract"] = media_contract(runtime)
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
            with color_source.open("rb") as colors, (prepared / "motion.rg16f").open("rb") as motions, raw.open("xb") as output:
                for index, frame in enumerate(manifest["frames"]):
                    check_cancel(cancelled)
                    if index % 64 == 0:
                        require_disk(job, plane * min(65, len(manifest["frames"]) - index), stage="NR 写入")
                    with phase("cache_read_verify"):
                        color, motion = colors.read(plane), motions.read(plane)
                        if (len(color) != plane or len(motion) != plane
                                or (verify_original and hashlib.sha256(color).hexdigest() != frame["color_sha256"])
                                or hashlib.sha256(motion).hexdigest() != frame["motion_sha256"]):
                            raise ValueError("Prepared media cache failed integrity check")
                    tick = time.perf_counter()
                    first_phase = "history_reset_first_frame" if lease and lease.reused else "first_frame_and_warmup"
                    with phase(first_phase if index == 0 else "nr_frames_and_transport"):
                        rendered = color if bypass else client.process(color, motion, frame["pts_ns"], reset=frame["reset"] or index == 0)
                    frame_seconds.append(time.perf_counter() - tick)
                    mix = profile.get("mix", 1.0)
                    if not bypass and mix < 1:
                        import numpy as np
                        a = np.frombuffer(color, np.uint8).astype(np.float32)
                        b = np.frombuffer(rendered, np.uint8).astype(np.float32)
                        rendered = np.rint(a * (1 - mix) + b * mix).clip(0, 255).astype(np.uint8).tobytes()
                    all_output_hash.update(rendered)
                    if include_history or frame["visible"]:
                        output.write(rendered)
                    if frame["visible"]:
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
            report["all_frames_sha256"] = all_output_hash.hexdigest()
            report["output_includes_history"] = include_history
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
                    "note": "Host wall time: includes NR, upload/download and transport/conversion; not GPU kernel time."}
            (job / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if raw.stat().st_size != plane * output_count:
        raise ValueError("NR pass output has an unexpected RGBA frame count")
    report["passed"] = True
    (job / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return raw, report


def render_effect(prepared: Path, manifest: dict, runtime: dict, effect: dict, root: Path, job: Path,
                  warmup: int, cancelled, progress) -> tuple[Path, dict]:
    """Run one or more NR passes without lossy intermediate video encoding."""
    profiles, plan = resolve_nr_parts(effect, runtime)
    if runtime.get("backend") == "owned_nr":
        for profile in profiles:
            owned_settings(profile_settings(profile, manifest["width"], manifest["height"], len(manifest["frames"]), warmup))
    previous = prepared / "color.rgba"
    reports = []
    total_frames = len(manifest["frames"]) * len(profiles)
    for index, profile in enumerate(profiles):
        final = index == len(profiles) - 1
        stage_job = job if len(profiles) == 1 else job / f"pass-{index + 1}"

        def stage_progress(stage, done, count, *, _index=index):
            progress(f"pass_{_index + 1}_of_{len(profiles)}:{stage}", _index * count + done, len(profiles) * count)

        with phase(f"nr_pass_{index + 1}"):
            raw, pass_report = _render_variant_frames(
                prepared, manifest, runtime, profile, root, stage_job, warmup, cancelled,
                stage_progress, color_source=previous, include_history=not final,
                verify_original=index == 0,
            )
        pass_report["pass_index"] = index + 1
        pass_report["look_inherited"] = plan["inherited"][index]
        reports.append(pass_report)
        if previous != prepared / "color.rgba":
            previous.unlink()
        previous = raw

    destination = job / "output.mp4"
    try:
        check_cancel(cancelled)
        from .storage_budget import require_disk
        require_disk(job, previous.stat().st_size * 2, stage="NR 结果编码")
        progress("encoding", total_frames, total_frames)
        with phase("encoding_output"):
            export_video(previous, destination, manifest, cancelled=cancelled)
        check_cancel(cancelled)
    except BaseException:
        from .resident_worker import resident_worker
        for pass_report in reversed(reports):
            resident = pass_report.get("resident")
            if resident and resident.get("retained"):
                resident_worker.request_release(resident["worker_id"], mode="idle")
                break
        raise
    previous.unlink()
    if len(reports) == 1:
        report = reports[0]
        report["effect"] = plan
    else:
        report = {
            "schema_version": 1,
            "kind": "nr_pass_stack_result",
            "pass_count": len(reports),
            "guide_policy": plan["guide_policy"],
            "intermediate_format": plan["intermediate_format"],
            "intermediate_video_encoding": False,
            "passes": reports,
            "raw_output_sha256": reports[-1]["raw_output_sha256"],
            "color_pipeline": manifest.get("color_pipeline"),
        }
    report["passed"] = True
    (job / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return destination, report


def render_variant(prepared: Path, manifest: dict, runtime: dict, profile: dict, root: Path, job: Path,
                   warmup: int, cancelled, progress) -> tuple[Path, dict]:
    """Backward-compatible entrypoint accepting one Look or an NR Pass Stack."""
    if manifest.get("streaming"):
        from .stream_render import render_stream
        return render_stream(manifest, runtime, profile, root, job, warmup, cancelled, progress)
    return render_effect(prepared, manifest, runtime, profile, root, job, warmup, cancelled, progress)


@measured("original_a_export")
def render_original(prepared: Path, manifest: dict, job: Path, cancelled) -> Path:
    if manifest.get("streaming"):
        from .stream_media import StreamEncoder, prepared_frames
        original = {**manifest, "guide_settings": asdict(GuideSettings(motion_provider="zero"))}
        job.mkdir(parents=True, exist_ok=False)
        with StreamEncoder(job / "original.mp4", original, cancelled) as encoder:
            for color, _motion, info in prepared_frames(original, GuideSettings(motion_provider="zero"), cancelled):
                if info["visible"]:
                    encoder.write(color)
            return encoder.finish()
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
