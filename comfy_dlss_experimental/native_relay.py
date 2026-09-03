"""Linux/native-Windows process client for the isolated Windows pipe relay."""
from __future__ import annotations

import contextlib
import hmac
import os
import secrets
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

HEADER = struct.Struct("<III")
MAGIC = 0x31524C43
MAX_PAYLOAD = 65536
INPUT, INPUT_END, CANCEL, OUTPUT, DIAGNOSTIC, EXIT, ERROR, HELLO = range(1, 9)
MAX_BUFFER = 256 * 1024 * 1024
MAX_LOG = 4 * 1024 * 1024
RUN_MARKER = "COMFY_DLSS_RELAY_RUN_ID"


def _marked_pidfds(marker: str) -> list[tuple[int, int]]:
    """Pin process identities before inspecting /proc; never kill by name/PID alone."""
    if not sys.platform.startswith("linux"):
        return []
    matches = []
    needle = f"{RUN_MARKER}={marker}".encode()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        descriptor = None
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            descriptor = os.pidfd_open(int(entry.name))
            if needle in (entry / "environ").read_bytes().split(b"\0"):
                matches.append((int(entry.name), descriptor))
                descriptor = None
        except (ProcessLookupError, FileNotFoundError, PermissionError):
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return matches


def _reap_marked_processes(marker: str) -> dict:
    processes = _marked_pidfds(marker)
    try:
        for _pid, descriptor in processes:
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        deadline = time.monotonic() + 0.5
        for _pid, descriptor in processes:
            remaining = max(0.0, deadline - time.monotonic())
            if not select.select([descriptor], [], [], remaining)[0]:
                with contextlib.suppress(ProcessLookupError):
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        deadline = time.monotonic() + 3
        for _pid, descriptor in processes:
            if not select.select([descriptor], [], [], max(0.0, deadline - time.monotonic()))[0]:
                raise RuntimeError("an owned test process did not terminate")
    finally:
        for _pid, descriptor in processes:
            os.close(descriptor)
    remaining = _marked_pidfds(marker)
    try:
        pids = [pid for pid, _descriptor in remaining]
    finally:
        for _pid, descriptor in remaining:
            os.close(descriptor)
    if pids:
        raise RuntimeError(f"owned processes remain after cleanup: {pids}")
    return {"reaped_processes": len(processes), "remaining_owned_processes": 0}


def windows_path(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if os.name == "nt":
        return str(resolved)
    # Proton's default Z: maps the Unix root; never guess user drive mappings.
    return "Z:" + str(resolved).replace("/", "\\")


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        piece = sock.recv(length - len(data))
        if not piece:
            raise EOFError("relay disconnected")
        data.extend(piece)
    return bytes(data)


class NativeRelay:
    """One external worker per instance. A bounded reader drains both output channels.

    Only write/cancel are thread-safe. Frame exchanges must have a single owner.
    This class does not claim NGX success: it only supervises transport/processes.
    """
    def __init__(self, relay: Path, worker: Path, job_dir: Path, *, proton: Path | None = None,
                 compatdata: Path | None = None, environment: dict[str, str] | None = None,
                 timeout: float = 240, worker_timeout: float | None = None):
        if not 1 <= timeout <= 3600:
            raise ValueError("timeout must be between 1 and 3600 seconds")
        worker_timeout = timeout if worker_timeout is None else worker_timeout
        if not 1 <= worker_timeout <= 3600:
            raise ValueError("worker_timeout must be between 1 and 3600 seconds")
        if not relay.is_file() or not worker.is_file():
            raise FileNotFoundError("relay or external worker is missing")
        if proton and (not proton.is_file() or compatdata is None):
            raise ValueError("Proton requires an existing executable and isolated compatdata")
        self.timeout = timeout
        self._cv = threading.Condition()
        self._writer = threading.Lock()
        self._chunks: deque[bytes] = deque()
        self._buffered = 0
        self._error: BaseException | None = None
        self._hello: dict | None = None
        self._exit: dict | None = None
        self._reader_thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._socket: socket.socket | None = None
        self._closed = False
        self._input_closed = False
        self._discard = False
        self._log_bytes = 0
        self._log_truncated = False
        self._run_marker = secrets.token_hex(16)
        self._prefix_lock = None
        self.cleanup: dict = {}
        job_dir.mkdir(parents=True, exist_ok=True)
        self.job_dir = job_dir
        # Exclusive creation prevents accidentally mixing two runs' evidence.
        self._launcher_log = (job_dir / "relay-launcher.log").open("xb")
        self._worker_log = None
        trace = None
        try:
            self._worker_log = (job_dir / "worker-stderr.log").open("xb")
            env = os.environ.copy()
            if environment:
                env.update(environment)
            env[RUN_MARKER] = self._run_marker
            from .execution_log import current_trace
            trace = current_trace()
            if trace:
                trace.marker = self._run_marker
            if proton:
                import fcntl
                compatdata.mkdir(parents=True, exist_ok=True)
                self._prefix_lock = (compatdata / "comfy-relay.lock").open("ab")
                # Reusing one Prefix concurrently defeats process ownership/isolation.
                fcntl.flock(self._prefix_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                env["STEAM_COMPAT_DATA_PATH"] = str(compatdata.resolve())
                env["PROTON_LOG"] = "1"
                env["PROTON_LOG_DIR"] = str(job_dir.resolve())
                env["SteamGameId"] = "0"
            token = secrets.token_bytes(32)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                listener.settimeout(min(timeout, 90))
                command = ([str(proton), "run", str(relay.resolve())] if proton else [str(relay.resolve())])
                command += ["--worker", windows_path(worker), "--port", str(listener.getsockname()[1]),
                            "--token", token.hex(), "--timeout-ms", str(int(worker_timeout * 1000))]
                self._process = subprocess.Popen(
                    command, cwd=job_dir, env=env, stdin=subprocess.DEVNULL,
                    stdout=self._launcher_log, stderr=self._launcher_log, start_new_session=os.name != "nt",
                )
                self._socket, address = listener.accept()
                # A frame header is followed by color/motion packets and then a
                # synchronous response. Nagle + delayed ACK can add ~40 ms to
                # every exchange even on localhost; do not delay small writes.
                self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._socket.settimeout(10)
                if address[0] != "127.0.0.1" or not hmac.compare_digest(_recv_exact(self._socket, 32), token):
                    raise RuntimeError("invalid relay authentication")
                self._socket.sendall(b"\x01")
                self._socket.settimeout(timeout)
            self._reader_thread = threading.Thread(target=self._reader, name="dlss-relay-reader", daemon=True)
            self._reader_thread.start()
            self._await(lambda: self._hello is not None, "worker startup")
        except BaseException:
            if trace:
                trace.set_worker_state("releasing")
            try:
                self.close()
            except BaseException:
                if trace:
                    trace.set_worker_state("cleanup_failed")
                raise
            if trace:
                trace.set_worker_state("released")
                trace.marker = None
            raise

    @property
    def hello(self) -> dict:
        return dict(self._hello or {})

    @property
    def run_marker(self) -> str:
        return self._run_marker

    @property
    def healthy(self) -> bool:
        with self._cv:
            return not self._closed and self._error is None and self._exit is None and not self._input_closed

    def _reader(self) -> None:
        try:
            while True:
                magic, kind, size = HEADER.unpack(_recv_exact(self._socket, HEADER.size))
                if magic != MAGIC or kind not in (OUTPUT, DIAGNOSTIC, EXIT, ERROR, HELLO) or size > MAX_PAYLOAD:
                    raise RuntimeError("invalid relay response packet")
                payload = _recv_exact(self._socket, size)
                with self._cv:
                    if kind == HELLO:
                        if self._hello is not None or size != 8:
                            raise RuntimeError("invalid duplicate/short relay hello")
                        relay_pid, worker_pid = struct.unpack("<II", payload)
                        self._hello = {"relay_pid": relay_pid, "worker_pid": worker_pid}
                    elif kind == OUTPUT and not self._discard:
                        if self._buffered + size > MAX_BUFFER:
                            raise RuntimeError("worker exceeded bounded output buffer")
                        self._chunks.append(payload)
                        self._buffered += size
                    elif kind == DIAGNOSTIC:
                        available = MAX_LOG - self._log_bytes
                        self._worker_log.write(payload[:available])
                        self._log_bytes += min(available, size)
                        if size > available and not self._log_truncated:
                            self._worker_log.write(b"\n[relay: log truncated at 4 MiB; remaining bytes drained]\n")
                            self._log_truncated = True
                        self._worker_log.flush()
                    elif kind == ERROR:
                        raise RuntimeError("relay startup: " + payload.decode("utf-8", "replace"))
                    elif kind == EXIT:
                        if size != 8:
                            raise RuntimeError("invalid relay exit packet")
                        code, reason = struct.unpack("<II", payload)
                        self._exit = {"exit_code": code, "reason": reason}
                        self._cv.notify_all()
                        return
                    self._cv.notify_all()
        except BaseException as exc:
            with self._cv:
                self._error = exc
                self._cv.notify_all()
            # A protocol failure must also release any writer blocked in sendall.
            with contextlib.suppress(OSError):
                self._socket.shutdown(socket.SHUT_RDWR)

    def _await(self, condition, stage: str) -> None:
        deadline = time.monotonic() + self.timeout
        with self._cv:
            while not condition():
                if self._error:
                    raise RuntimeError(f"{stage}: {self._error}") from self._error
                if self._exit is not None:
                    raise EOFError(f"{stage}: worker exited {self._exit}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{stage} timed out")
                self._cv.wait(remaining)

    def _send(self, kind: int, payload: bytes | memoryview = b"") -> None:
        if self._closed or self._socket is None:
            raise RuntimeError("relay is closed")
        if len(payload) > MAX_PAYLOAD:
            raise ValueError("relay payload too large")
        with self._writer:
            self._socket.sendall(HEADER.pack(MAGIC, kind, len(payload)))
            if payload:
                self._socket.sendall(payload)

    def write(self, data: bytes) -> None:
        if self._input_closed:
            raise RuntimeError("worker stdin already closed")
        source = memoryview(data)
        for offset in range(0, len(source), MAX_PAYLOAD):
            self._send(INPUT, source[offset:offset + MAX_PAYLOAD])

    def read_exact(self, count: int) -> bytes:
        if not 0 <= count <= MAX_BUFFER:
            raise ValueError("invalid read length")
        self._await(lambda: self._buffered >= count, "worker output")
        data = bytearray()
        with self._cv:
            while len(data) < count:
                chunk = self._chunks.popleft()
                take = min(len(chunk), count - len(data))
                data.extend(chunk[:take])
                self._buffered -= take
                if take < len(chunk):
                    self._chunks.appendleft(chunk[take:])
        return bytes(data)

    def close_input(self) -> None:
        if not self._input_closed:
            self._send(INPUT_END)
            self._input_closed = True

    def wait(self) -> dict:
        self._await(lambda: self._exit is not None, "worker exit")
        if self._buffered:
            raise RuntimeError("worker returned unexpected trailing output")
        return dict(self._exit)

    def cancel(self) -> None:
        with self._cv:
            self._discard = True
            self._chunks.clear()
            self._buffered = 0
        if self._exit is not None or self._socket is None:
            return
        # Cancellation must not wait behind a writer blocked by a stalled child.
        if not self._writer.acquire(timeout=0.1):
            self._socket.shutdown(socket.SHUT_RDWR)
            return
        try:
            packet = HEADER.pack(MAGIC, CANCEL, 0)
            if not select.select([], [self._socket], [], 0.1)[1] or self._socket.send(packet) != len(packet):
                self._socket.shutdown(socket.SHUT_RDWR)
        finally:
            self._writer.release()

    def close(self) -> None:
        if self._closed:
            return
        if self._socket is not None:
            with contextlib.suppress(OSError, RuntimeError):
                self.cancel()
            with contextlib.suppress(OSError):
                self._socket.shutdown(socket.SHUT_RDWR)
            self._socket.close()
        try:
            self._stop_process()
        finally:
            if self._reader_thread:
                self._reader_thread.join(timeout=2)
                if self._reader_thread.is_alive():
                    # Keep handles alive and allow close() to be retried.
                    raise RuntimeError("relay reader did not stop after socket shutdown")
            if self._worker_log:
                self._worker_log.close()
            self._launcher_log.close()
        # Retain the prefix lock if process cleanup failed. Another run must not
        # reuse that prefix before a successful close() retry proves it is idle.
        if self._prefix_lock:
            self._prefix_lock.close()
        self._closed = True

    def _stop_process(self) -> None:
        if self._process is not None:
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if sys.platform.startswith("linux"):
                    self.cleanup = _reap_marked_processes(self._run_marker)
                elif os.name != "nt":
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self._process.pid, signal.SIGTERM)
                else:
                    self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(self._process.pid, signal.SIGKILL)
                    else:
                        self._process.kill()
                    self._process.wait(timeout=5)
            if sys.platform.startswith("linux"):
                # Proton's wrapper can exit before detached Wine children do.
                final = _reap_marked_processes(self._run_marker)
                final["reaped_processes"] += self.cleanup.get("reaped_processes", 0)
                self.cleanup = final

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
