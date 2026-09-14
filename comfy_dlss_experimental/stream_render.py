"""Continuous per-layer NR histories, bounded CPU frames, no raw disk intermediates."""
from __future__ import annotations

import contextlib
from dataclasses import asdict
import hashlib
import json
import threading

from .direct_nr import DirectNRClient
from .execution_log import current_trace, phase
from .flow_provider import FlowProvider
from .native_relay import NativeRelay
from .processing_plan import resolve_nr_parts
from .resident_worker import STREAM_CAPACITY, compatibility_key, resident_worker
from .storage_manager import check_job
from .stream_media import StreamEncoder, prepared_frames
from .temporal_guides import GuideSettings
from .owned_adapter import OwnedMediaClient, launch_owned, media_contract, owned_settings


def render_stream(manifest, runtime, effect, root, job, warmup, cancelled, progress):
    from .video_pipeline import _GPU_LOCK, cancellable_lock, check_cancel, profile_settings, relay_environment, snapshot_runtime
    profiles, plan = resolve_nr_parts(effect, runtime)
    values = dict(manifest["guide_settings"])
    if values.get("flow"):
        values["flow"] = FlowProvider(**values["flow"])
    guides = GuideSettings(**values)
    job.mkdir(parents=True, exist_ok=False)
    trace = current_trace()
    count = len(manifest["frames"])
    owned = runtime.get("backend") == "owned_nr"
    client_factory = OwnedMediaClient if owned else DirectNRClient
    # Preflight every stage before launching any Worker in the stack.
    if owned:
        for profile in profiles:
            owned_settings(profile_settings(profile, manifest["width"], manifest["height"], count, warmup))
    persistent = runtime.get("worker_policy") == "persistent" and len(profiles) == 1 and count < STREAM_CAPACITY
    sessions, clients, reports, leases = [], [], [], []
    hashes = [hashlib.sha256() for _ in profiles]
    full_hashes = [hashlib.sha256() for _ in profiles]
    stop = threading.Event()
    complete = False
    frame_iterator = None

    def watch():
        while not stop.wait(.1):
            if cancelled():
                for session in list(sessions):
                    if session:
                        with contextlib.suppress(OSError, RuntimeError):
                            session.cancel()
                return

    with cancellable_lock(_GPU_LOCK, cancelled):
        watcher = threading.Thread(target=watch, daemon=True, name="dlss-stream-cancel")
        watcher.start()
        try:
            if not persistent or all(not p.get("nr_enabled", True) or p.get("mix", 1) == 0 for p in profiles):
                resident_worker.retire_idle("streaming_stack_or_isolated")
            for index, profile in enumerate(profiles):
                check_cancel(cancelled)
                settings = profile_settings(profile, manifest["width"], manifest["height"], count, warmup)
                bypass = not profile.get("nr_enabled", True) or profile.get("mix", 1) == 0
                report = {"settings": asdict(settings), "profile": profile, "bypassed": bypass, "passed": False,
                          "pass_index": index + 1, "look_inherited": plan["inherited"][index],
                          "execution_contract": media_contract(runtime)}
                reports.append(report)
                sessions.append(None)
                clients.append(None)
                leases.append(None)
                if bypass:
                    continue
                if trace:
                    trace.set_worker_state("starting")
                relay, worker = snapshot_runtime(runtime, root)
                proton, environment = relay_environment(runtime, root)
                runtime_key = runtime.get("runtime_key", "")
                if len(runtime_key) != 64 or any(c not in "0123456789abcdef" for c in runtime_key):
                    raise ValueError("Invalid runtime fingerprint")

                def launch():
                    with phase("worker_startup"):
                        if owned:
                            return launch_owned(runtime, relay, worker, job / f"worker-{index + 1}",
                                root, proton, environment, f"-stream-{index + 1}" if len(profiles) > 1 else "")
                        return NativeRelay(relay, worker, job / f"worker-{index + 1}", proton=proton,
                            compatdata=root / "prefixes" / ("direct-" + runtime_key[:24] + (f"-stream-{index + 1}" if len(profiles) > 1 else "")),
                            environment=environment, timeout=1020, worker_timeout=3600)

                if persistent:
                    with phase("worker_acquire"):
                        lease = resident_worker.acquire(key=compatibility_key(runtime, settings, environment, owner_root=root),
                            runtime=runtime, settings=settings, factory=launch,
                            idle_seconds=runtime.get("idle_timeout_seconds", 300), trace=trace,
                            client_factory=client_factory)
                    sessions[index], clients[index], leases[index] = lease.session, lease.client, lease
                else:
                    session = launch()
                    sessions[index] = session
                    clients[index] = client_factory(session, settings)
                if trace:
                    trace.markers = [s.run_marker for s in sessions if s]
                    trace.record["worker_instances"] = len(trace.markers)
                    trace.record["effective_worker_policy"] = "persistent" if persistent else "isolated"
                    trace.record["worker_reused"] = bool(leases[index] and leases[index].reused)
                    trace.record["execution_contract"] = media_contract(runtime)
                    trace.set_worker_state("running")
            with StreamEncoder(job / "output.mp4", manifest, cancelled) as encoder:
                frame_iterator = prepared_frames(manifest, guides, cancelled)
                for frame_index, (original, motion, frame) in enumerate(frame_iterator):
                    check_cancel(cancelled)
                    color = original
                    for index, (client, profile) in enumerate(zip(clients, profiles)):
                        first = "history_reset_first_frame" if leases[index] and leases[index].reused else "first_frame_and_warmup"
                        with phase(first if frame_index == 0 else "nr_frames_and_transport"):
                            rendered = color if client is None else client.process(color, motion, frame["pts_ns"], reset=frame["reset"] or frame_index == 0)
                        mix = profile.get("mix", 1)
                        if client is not None and mix < 1:
                            import numpy as np
                            a = np.frombuffer(color, np.uint8).astype(np.float32)
                            b = np.frombuffer(rendered, np.uint8).astype(np.float32)
                            rendered = np.rint(a * (1 - mix) + b * mix).clip(0, 255).astype(np.uint8).tobytes()
                        color = rendered
                        full_hashes[index].update(color)
                        if frame["visible"]:
                            hashes[index].update(color)
                    if frame["visible"]:
                        with phase("encoding_output"):
                            encoder.write(color)
                    progress("streaming_nr", frame_index + 1, count)
                    if frame_index % 32 == 0:
                        check_job()
                for index, client in enumerate(clients):
                    if client is not None and not leases[index]:
                        with phase("worker_finish"):
                            reports[index]["exit"] = client.finish()
                with phase("encoding_output"):
                    destination = encoder.finish()
            check_cancel(cancelled)
            complete = True
        finally:
            stop.set()
            watcher.join(timeout=2)
            errors = []
            if frame_iterator is not None and hasattr(frame_iterator, "close"):
                try:
                    frame_iterator.close()
                except BaseException as exc:
                    errors.append(str(exc))
            for index in reversed(range(len(sessions))):
                session, lease = sessions[index], leases[index]
                try:
                    if lease:
                        reports[index]["resident"] = resident_worker.finish(lease, complete=complete and not cancelled())
                    elif session:
                        if trace:
                            trace.set_worker_state("releasing")
                        with phase("worker_cleanup"):
                            session.close()
                        reports[index]["cleanup"] = session.cleanup
                except BaseException as exc:
                    errors.append(str(exc))
            if trace:
                trace.markers = []
                trace.marker = None
                if errors:
                    trace.set_worker_state("cleanup_failed")
                elif not persistent:
                    trace.set_worker_state("released")
            if errors:
                raise RuntimeError("Worker cleanup failed: " + "; ".join(errors))
    for index, report in enumerate(reports):
        report.update(passed=True, raw_output_sha256=hashes[index].hexdigest(),
                      all_frames_sha256=full_hashes[index].hexdigest(), successful_output_frames=count)
    result = {"passed": True, "effect": plan, "pass_count": len(profiles), "passes": reports,
              "raw_output_sha256": hashes[-1].hexdigest(), "color_pipeline": manifest["color_pipeline"],
              "storage_mode": "streaming", "raw_disk_bytes": 0, "intermediate_video_encoding": False,
              "worker_instances": sum(c is not None for c in clients),
              "note": "One continuous independent NR history per layer; original motion reused across layers. Multi-layer workers are released after this task."}
    if len(profiles) == 1:
        result.update(reports[0])
    (job / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return destination, result
