"""One bounded, opt-in resident D5V2 stream. Not a pool of GPU workers.

The external worker cannot reconfigure NR controls. Reuse requires the same
runtime and immutable video header (except frame count); each lease starts with
an explicit history reset. Failed/partial leases are never reused.
"""
from __future__ import annotations

import atexit
import copy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

from .direct_nr import DirectNRClient

STREAM_CAPACITY = 65_536
MAX_AGE_SECONDS = 1800
VERIFIED_PAIR = (
    "99ef1f2976d9cd16b7fc269adb6c6450fb64c81a522c9b9e6edc6a28201dc904",
    "28bdc080d28686decdb63f6f4246b022274916b80aafdab266fe0fb63b2b9265",
)


def persistent_supported(hashes):
    return (hashes.get("worker"), hashes.get("nvngx_dlssnr")) == VERIFIED_PAIR


def compatibility_key(runtime, settings, environment, *, owner_root=None):
    header = asdict(settings)
    header.pop("frame_count")
    return hashlib.sha256(json.dumps({"runtime": runtime["runtime_key"], "header": header,
        "owner_root": str(Path(owner_root).resolve()) if owner_root is not None else None,
        "display": {key: environment.get(key) for key in ("DISPLAY", "XAUTHORITY", "WINE_GRAPHICS_DRIVER")}},
        sort_keys=True).encode()).hexdigest()


class ResidentLease:
    def __init__(self, entry, requested_frames, reused):
        self.entry = entry
        self.session = entry["session"]
        self.client = entry["client"]
        self.start_index = self.client.index
        self.requested_frames = requested_frames
        self.reused = reused
        self.worker_id = entry["worker_id"]
        self.finished = False


class ResidentWorker:
    def __init__(self, *, clock=time.monotonic, background=True):
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._entry = None
        self._last = None
        self._clock = clock
        self._background = background
        self._thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()

    def acquire(self, *, key, runtime, settings, factory, idle_seconds, trace=None, client_factory=DirectNRClient):
        if type(idle_seconds) is not int or not 30 <= idle_seconds <= 900:
            raise ValueError("Resident idle timeout must be 30..900 seconds")
        if settings.frame_count >= STREAM_CAPACITY:
            raise ValueError("This resident stream supports fewer than 65536 frames per task; use isolated mode")
        with self._operation:
            if self._stop.is_set():
                raise RuntimeError("Resident manager is shutting down")
            with self._lock:
                entry = self._entry
                if entry and entry["state"] == "running":
                    raise RuntimeError("Resident worker already leased; NR execution must remain serialized")
                reusable = bool(entry and entry["state"] == "idle" and not entry["retire"]
                    and not entry["cancel"].is_set() and entry["key"] == key and entry["session"].healthy
                    and self._clock() < entry["idle_deadline"]
                    and self._clock() - entry["created_mono"] < MAX_AGE_SECONDS
                    and entry["client"].index + settings.frame_count < entry["client"].settings.frame_count)
                if reusable:
                    # Reserve while holding the same lock as manual idle release.
                    entry.update(state="running", trace=trace, idle_seconds=idle_seconds)
            if not reusable:
                if entry:
                    self._drop(entry, "incompatible_or_expired")
                session = factory()
                try:
                    client = client_factory(session, replace(settings, frame_count=STREAM_CAPACITY))
                except BaseException:
                    session.close()
                    raise
                entry = {"worker_id": uuid.uuid4().hex, "key": key, "session": session, "client": client,
                    "runtime": copy.deepcopy(runtime), "settings": asdict(settings), "trace": trace,
                    "state": "running", "created_at": time.time(), "created_mono": self._clock(),
                    "idle_deadline": None, "idle_seconds": idle_seconds, "leases": 0, "resources": None,
                    "cancel": threading.Event(), "retire": False, "last_sample_mono": 0.0, "error": None}
                with self._lock:
                    self._entry = entry
            with self._lock:
                entry["leases"] += 1
                entry["execution_id"] = trace.id if trace else uuid.uuid4().hex
                if trace:
                    trace.marker = entry["session"].run_marker
                    trace.set_worker_state("running")
                    trace.record.update(worker_id=entry["worker_id"], worker_reused=reusable)
                lease = ResidentLease(entry, settings.frame_count, reusable)
                if self._background and self._thread is None:
                    self._thread = threading.Thread(target=self._loop, daemon=True, name="dlss-resident-lifecycle")
                    self._thread.start()
                return lease

    def finish(self, lease, *, complete):
        """Called exactly once, with the render serialization lock still held."""
        with self._operation:
            with self._lock:
                entry = lease.entry
                if lease.finished or self._entry is not entry:
                    raise RuntimeError("Invalid or already returned resident lease")
                lease.finished = True
                trace = entry["trace"]
                valid = (complete and not entry["client"].failed and entry["session"].healthy
                         and entry["client"].index - lease.start_index == lease.requested_frames
                         and not entry["cancel"].is_set() and not (trace and trace.release_requested.is_set()))
                retain = valid and not entry["retire"] and not self._stop.is_set()
            if retain and hasattr(entry["client"], "end_task"):
                try:
                    entry["client"].end_task()
                except BaseException:
                    self._drop(entry, "end_task_failed")
                    raise
            with self._lock:
                # A manual release can arrive while END is in flight.
                retain = retain and not entry["retire"] and not self._stop.is_set()
                if retain:
                    entry.update(state="idle", trace=None, execution_id=None,
                                 idle_deadline=self._clock() + entry["idle_seconds"])
                    if trace:
                        trace.marker = None
                        trace.set_worker_state("idle")
                    self._wake.set()
                    return {"retained": True, "worker_id": entry["worker_id"], "reused": lease.reused,
                            "history_reset": True, "initial_warmup_reused": lease.reused}
            self._drop(entry, "task_failed_or_release_requested")
            if trace:
                trace.marker = None
                trace.set_worker_state("released")
            return {"retained": False, "worker_id": entry["worker_id"], "reused": lease.reused,
                    "cleanup": copy.deepcopy(entry["session"].cleanup)}

    def retire_idle(self, reason="isolated_policy"):
        with self._operation:
            with self._lock:
                entry = self._entry
                if entry and entry["state"] == "running":
                    raise RuntimeError("Cannot replace an actively leased resident worker")
            if entry:
                self._drop(entry, reason)

    def _drop(self, entry, reason):
        # _operation is held. Never hold the metadata lock across OS cleanup.
        with self._lock:
            entry.update(state="releasing", release_reason=reason)
            trace = entry["trace"]
            if trace:
                trace.set_worker_state("releasing")
        try:
            entry["session"].close()
        except BaseException as exc:
            with self._lock:
                entry.update(state="cleanup_failed", error=str(exc), trace=None)
                entry["cancel"].clear()
                entry["retire"] = False
                if trace:
                    trace.set_worker_state("cleanup_failed")
            raise
        with self._lock:
            entry.update(state="released", trace=None, execution_id=None)
            self._last = self._public(entry)
            self._last["cleanup"] = copy.deepcopy(entry["session"].cleanup)
            if self._entry is entry:
                self._entry = None

    def request_release(self, worker_id, *, mode="idle", execution_id=None):
        with self._lock:
            entry = self._entry
            if entry is None or entry["worker_id"] != worker_id:
                return {"accepted": False, "reason": "stale_worker_id"}
            busy = entry["state"] == "running"
            if mode not in {"idle", "after_task", "cancel"}:
                return {"accepted": False, "reason": "invalid_release_mode"}
            if busy and (mode == "idle" or not execution_id or execution_id != entry.get("execution_id")):
                return {"accepted": False, "reason": "worker_busy_or_stale_execution"}
            entry["retire"] = True
            if busy and mode == "cancel":
                entry["cancel"].set()
                if entry["trace"]:
                    entry["trace"].release_requested.set()
            self._wake.set()
            return {"accepted": True, "worker_id": worker_id,
                    "state": "release_after_task" if busy and mode == "after_task" else "release_requested"}

    def _public(self, entry):
        return {"worker_id": entry["worker_id"], "state": entry["state"], "created_at": entry["created_at"],
            "protocol": getattr(entry["client"], "protocol", "D5V2"),
            "execution_id": entry.get("execution_id"), "runtime": copy.deepcopy(entry["runtime"]),
            "settings": entry["settings"], "leases": entry["leases"], "stream_frames": entry["client"].index,
            "idle_seconds": entry["idle_seconds"], "idle_remaining_seconds":
                max(0, entry["idle_deadline"] - self._clock()) if entry["state"] == "idle" else None,
            "resources": copy.deepcopy(entry["resources"]), "release_pending": entry["retire"],
            "log_directory": str(getattr(entry["session"], "job_dir", "")),
            "error": entry.get("error"), "release_reason": entry.get("release_reason")}

    def snapshot(self):
        with self._lock:
            return {"workers": [self._public(self._entry)] if self._entry else [], "last_release": copy.deepcopy(self._last)}

    def maintain(self, *, sample=True):
        # Never let telemetry or an idle timer race a lease transition/close.
        if not self._operation.acquire(blocking=False):
            return
        try:
            with self._lock:
                entry = self._entry
                if entry is None or entry["state"] == "running":
                    return  # active tasks already have their own resource sampler
                expired = entry["state"] == "idle" and (self._clock() >= entry["idle_deadline"]
                    or self._clock() - entry["created_mono"] >= MAX_AGE_SECONDS or not entry["session"].healthy)
            if expired or entry["retire"]:
                self._drop(entry, "manual_release" if entry["retire"] else "idle_timeout_or_worker_exit")
                return
            if sample and entry["state"] == "idle" and self._clock() - entry["last_sample_mono"] >= 1:
                from .execution_log import resource_snapshot
                resources = resource_snapshot(entry["session"].run_marker, "idle")
                with self._lock:
                    entry.update(resources=resources, last_sample_mono=self._clock())
        finally:
            self._operation.release()

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait(.5)
            self._wake.clear()
            try:
                self.maintain()
            except (OSError, RuntimeError):
                pass  # cleanup failure remains visible; no automatic retry loop

    def shutdown(self):
        self._stop.set()
        self._wake.set()
        with self._lock:
            entry = self._entry
            if entry and entry["state"] == "running":
                entry["retire"] = True
                entry["cancel"].set()
                if entry["trace"]:
                    entry["trace"].release_requested.set()
                entry["session"].cancel()
                return  # the owner releases its lease; never close its handles concurrently
        self.retire_idle("server_shutdown")
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)


resident_worker = ResidentWorker()
atexit.register(resident_worker.shutdown)
