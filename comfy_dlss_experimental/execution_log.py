"""Per-execution diagnostics. No dependency on Comfy, NVML, or psutil.

GPU utilization is device-wide, not attributable to one worker. Process RSS
is summed (shared pages may be counted twice). Unavailable values stay null.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
import warnings
import xml.etree.ElementTree as ET

_current = ContextVar("dlss_execution", default=None)
_active = {}
_lock = threading.RLock()
_gpu_lock = threading.Lock()
_gpu_cache = None
_gpu_checked_at = 0.0
RESOURCE_SAMPLE_SECONDS = 1.0


def current_trace():
    return _current.get()


@contextmanager
def phase(name):
    trace = current_trace()
    start = time.perf_counter()
    if trace:
        with _lock:
            previous = trace.stage
            trace.stage = name
    try:
        yield
    finally:
        if trace:
            with _lock:
                trace.stages[name] = trace.stages.get(name, 0) + time.perf_counter() - start
                trace.stage = previous


def measured(name):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with phase(name):
                return function(*args, **kwargs)
        return call
    return decorate


def _number(value):
    try:
        return float(value.split()[0])
    except (ValueError, AttributeError, IndexError):
        return None


def gpu_snapshot():
    try:
        result = subprocess.run(["nvidia-smi", "-q", "-x"], capture_output=True, text=True, timeout=2, check=True)
        tree = ET.fromstring(result.stdout)
        return {"available": True, "driver": tree.findtext("driver_version"), "gpus": [
            {"name": gpu.findtext("product_name"), "uuid": gpu.findtext("uuid"),
             "memory_used_mib": _number(gpu.findtext("fb_memory_usage/used")),
             "memory_total_mib": _number(gpu.findtext("fb_memory_usage/total")),
             "utilization_percent": _number(gpu.findtext("utilization/gpu_util")),
             "processes": [{"pid": int(p.findtext("pid", "0")),
                            "memory_mib": _number(p.findtext("used_memory"))}
                           for p in gpu.findall("processes/process_info")]}
            for gpu in tree.findall("gpu")]}
    except (OSError, ValueError, subprocess.SubprocessError, ET.ParseError) as exc:
        return {"available": False, "reason": type(exc).__name__, "gpus": []}


def cached_gpu_snapshot():
    global _gpu_cache, _gpu_checked_at
    # The live UI and task sampler share one query; opening a card must not add
    # a second nvidia-smi process each second during a short preview.
    with _gpu_lock:
        if _gpu_cache is None or time.monotonic() - _gpu_checked_at >= RESOURCE_SAMPLE_SECONDS:
            _gpu_cache = gpu_snapshot() | {"sampled_at": time.time()}
            _gpu_checked_at = time.monotonic()
        return copy.deepcopy(_gpu_cache)


def owned_resources(marker):
    if isinstance(marker, (list, tuple)):
        groups = [owned_resources(m) for m in set(marker)]
        processes = {p["pid"]: p for group in groups for p in group["processes"]}
        available = any(g["available"] for g in groups)
        return {"available": available, "processes": list(processes.values()),
                "rss_mib": sum(p["rss_mib"] for p in processes.values()) if available else None,
                "cpu_seconds": sum(p["cpu_seconds"] for p in processes.values()) if available else None}
    if not marker or not sys.platform.startswith("linux"):
        return {"available": False, "processes": [], "rss_mib": None, "cpu_seconds": None}
    # Match only this launched process family, never all Wine/NVIDIA processes.
    from .native_relay import _marked_pidfds
    processes = []
    pinned = _marked_pidfds(marker)
    try:
        for pid, _fd in pinned:
            try:
                root = Path("/proc") / str(pid)
                status = (root / "status").read_text()
                rss = next((int(line.split()[1]) / 1024 for line in status.splitlines()
                            if line.startswith("VmRSS:")), 0.0)
                fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
                cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
                processes.append({"pid": pid, "name": (root / "comm").read_text().strip(),
                                  "rss_mib": rss, "cpu_seconds": cpu})
            except (OSError, ValueError, IndexError):
                continue  # the exact process can exit between two reads
    finally:
        for _pid, fd in pinned:
            os.close(fd)
    return {"available": True, "processes": processes,
            "rss_mib": sum(p["rss_mib"] for p in processes),
            "cpu_seconds": sum(p["cpu_seconds"] for p in processes)}


def resource_snapshot(marker, worker_state):
    resource = owned_resources(marker)
    gpu = cached_gpu_snapshot()
    pids = {p["pid"] for p in resource["processes"]}
    matched = [p["memory_mib"] for g in gpu["gpus"] for p in g["processes"]
               if p["pid"] in pids and p["memory_mib"] is not None]
    return {"sampled_at": time.time(), "worker_state_at_sample": worker_state, "worker": resource,
            "worker_vram_mib": sum(matched) if matched else None, "gpu": gpu}


class ExecutionTrace:
    def __init__(self, kind, kwargs):
        self.id = uuid.uuid4().hex
        self.started = time.perf_counter()
        self.stage = "validation"
        self.stages = {}
        self.marker = None
        self.markers = []
        self.worker_state = "not_started"
        self.release_requested = threading.Event()
        self.preview = None
        self.report_file = None
        self.resources = None
        self.peak_rss = None
        self.peak_vram = None
        self._stop = threading.Event()
        runtime = kwargs.get("runtime", {})
        self.record = {"schema_version": 1, "execution_id": self.id, "kind": kind,
                       "node_id": kwargs.get("node_id"), "workflow_id": kwargs.get("workflow_id"),
                       "created_at": time.time(), "state": "running",
                       "parameters": {k: copy.deepcopy(v) for k, v in kwargs.items()
                                      if k in {"contract", "profile", "profile_a", "profile_b", "start_time",
                                               "duration", "preview_scale", "scale", "preview_mode", "cursor_time",
                                               "process_to_end", "retain_prepared_cache"}},
                       "input": copy.deepcopy(kwargs.get("sequence", {}).get("public", {})),
                       "guides": copy.deepcopy(kwargs.get("sequence", {}).get("settings", {})),
                       "color_policy": copy.deepcopy(kwargs.get("sequence", {}).get("color_policy", {})),
                       "runtime": {k: copy.deepcopy(runtime.get(k)) for k in
                                   ("backend", "platform", "host_platform", "execution_mode", "proton", "linux_display_backend", "worker_policy",
                                    "preset_id", "preset_path", "runtime_key", "components", "component_hashes", "idle_timeout_seconds")}}
        self.sampler = threading.Thread(target=self._sample_loop, daemon=True, name="dlss-resource-sample")

    def set_worker_state(self, state):
        with _lock:
            self.worker_state = state

    def _sample_loop(self):
        while not self._stop.wait(RESOURCE_SAMPLE_SECONDS):
            try:
                with _lock:
                    marker, worker_state = self.markers or self.marker, self.worker_state
                if not marker or worker_state not in {"starting", "running", "releasing"}:
                    continue
                sample = resource_snapshot(marker, worker_state)
                resource, vram = sample["worker"], sample["worker_vram_mib"]
                with _lock:
                    self.resources = sample
                    if resource["rss_mib"] is not None:
                        self.peak_rss = max(self.peak_rss or 0, resource["rss_mib"])
                    if vram is not None:
                        self.peak_vram = max(self.peak_vram or 0, vram)
            except (OSError, RuntimeError):
                pass  # telemetry must not change rendering success

    def snapshot(self):
        with _lock:
            return copy.deepcopy({**self.record, "stage": self.stage,
                                  "elapsed_seconds": time.perf_counter() - self.started,
                                  "stage_seconds": self.stages, "resources": self.resources,
                                  "worker_state": self.worker_state,
                                  "release_requested": self.release_requested.is_set(),
                                  "resource_sample_seconds": RESOURCE_SAMPLE_SECONDS,
                                  "sampled_peak_rss_mib": self.peak_rss,
                                  "sampled_peak_worker_vram_mib": self.peak_vram})


def tracked(kind):
    def decorate(function):
        @wraps(function)
        def call(**kwargs):
            from .config import data_root
            trace = ExecutionTrace(kind, kwargs)
            token = _current.set(trace)
            from .storage_manager import LOCK
            with LOCK, _lock:
                _active[trace.id] = trace
            trace.sampler.start()
            try:
                result = function(**kwargs)
                trace.record["state"] = "success"
                report = result[0] if kind == "preview" else result[1]
                trace.record["result"] = copy.deepcopy(report)
                return result
            except BaseException as exc:
                trace.record.update(state="cancelled" if isinstance(exc, InterruptedError) else "failed",
                                    error=str(exc), error_type=type(exc).__name__)
                raise
            finally:
                trace._stop.set()
                trace.sampler.join(timeout=3)
                trace.stage = trace.record["state"]
                snapshot = trace.snapshot()
                if trace.record["state"] == "success" and kind == "process":
                    result[1]["execution"] = snapshot | {"result": None}
                try:
                    directory = data_root() / "executions"
                    directory.mkdir(parents=True, exist_ok=True)
                    target = directory / (trace.id + ".json")
                    temporary = target.with_suffix(".partial")
                    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                    temporary.replace(target)
                    from .storage_manager import prune_execution_records
                    prune_execution_records(data_root())
                    if trace.preview is not None:
                        from .preview import preview_sessions, send_preview_event
                        public = preview_sessions.update(trace.preview, execution=snapshot | {"result": None})
                        send_preview_event(public, trace.preview.client_id)
                    if trace.report_file is not None:
                        existing = json.loads(trace.report_file.read_text())
                        existing["execution"] = snapshot | {"result": None}
                        trace.report_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                except (OSError, ValueError, TypeError) as exc:
                    warnings.warn(f"DLSS execution diagnostics could not be saved: {exc}")
                finally:
                    with _lock:
                        _active.pop(trace.id, None)
                    _current.reset(token)
        return call
    return decorate


def request_worker_release(execution_id):
    """Request cancellation through the owning job; never accept raw PIDs/paths.

    Cleanup remains in render_variant's finally block. An accepted request is
    not a claim that processes are already gone. A stale ID cannot cancel the
    next execution even if it uses the same preset or recycled OS process IDs.
    """
    with _lock:
        trace = _active.get(execution_id)
        if trace is None:
            return {"accepted": False, "reason": "execution_not_active"}
        if trace.worker_state not in {"starting", "running", "releasing"}:
            return {"accepted": False, "reason": "no_live_worker"}
        trace.release_requested.set()
        return {"accepted": True, "execution_id": trace.id, "state": "release_requested"}


def execution_status(limit=15, include_gpu=False):
    from .config import data_root
    with _lock:
        active = [t.snapshot() for t in _active.values()]
    directory = data_root() / "executions"
    history = []
    if directory.is_dir():
        files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        for path in files:
            try:
                record = json.loads(path.read_text())
                # Full result stays on disk; avoid sending a manifest per refresh.
                record.pop("result", None)
                history.append(record)
            except (OSError, ValueError):
                continue
    device = cached_gpu_snapshot() if include_gpu else None
    from .resident_worker import resident_worker
    return {"active": active, "history": history, "concurrency": 1, "gpu": device,
            "resident": resident_worker.snapshot(),
            "resource_sample_seconds": RESOURCE_SAMPLE_SECONDS,
            "lifecycle": {"default_policy": "isolated", "lazy_start": True, "persistent_supported": True,
                          "persistent_scope": "verified D5V2 worker/model pair; identical NR controls and dimensions"},
            "note": "GPU utilization is device-wide; RSS sums owned processes; peaks are sampled every 1s."}
