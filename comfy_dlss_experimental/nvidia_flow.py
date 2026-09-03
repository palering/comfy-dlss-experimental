"""Native OS-specific NVOF helpers; never load driver DLLs in Comfy Python."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

from .flow_provider import FlowProvider

MAGIC = 0x31464F4E
HEADER = struct.Struct("<4I")


def helper_path() -> Path:
    from .helper_artifacts import find_helper
    return find_helper("nvof")


class PipeProcess:
    """One serial caller per process. Bounded stderr, nonblocking pipes, cancellation."""
    def __new__(cls, *args, **kwargs):
        if sys.platform == "win32":
            from .windows_pipe import WindowsPipeProcess
            return WindowsPipeProcess(*args, **kwargs)
        return super().__new__(cls)
    def __init__(self, command, cancelled=lambda: False, timeout=30):
        self.cancelled, self.timeout = cancelled, timeout
        self.stderr = bytearray()
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=0, close_fds=True)
        try:
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                os.set_blocking(pipe.fileno(), False)
        except BaseException:
            self.close()
            raise

    def exchange(self, payload: bytes, count: int | None):
        import selectors  # POSIX transport only; not used for Windows pipes.
        process = self.process
        result = bytearray()
        outgoing = memoryview(payload)
        deadline = time.monotonic() + self.timeout
        limit = 4096 if count is None else count
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "out")
            selector.register(process.stderr, selectors.EVENT_READ, "err")
            if outgoing:
                selector.register(process.stdin, selectors.EVENT_WRITE, "in")
            while True:
                if self.cancelled():
                    raise InterruptedError("NVIDIA 光流计算已取消")
                if time.monotonic() >= deadline:
                    raise TimeoutError("NVIDIA 光流辅助程序超时")
                for key, _events in selector.select(.1):
                    if key.data == "in":
                        try:
                            written = os.write(key.fd, outgoing[:65536])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError as error:
                            raise RuntimeError("NVIDIA 光流辅助程序关闭了输入管道") from error
                        outgoing = outgoing[written:]
                        if not outgoing:
                            selector.unregister(key.fileobj)
                        continue
                    try:
                        block = os.read(key.fd, 65536 if key.data == "err" else min(65536, limit - len(result)))
                    except BlockingIOError:
                        continue
                    if key.data == "err":
                        self.stderr.extend(block)
                        del self.stderr[:-8192]
                        if not block:
                            selector.unregister(key.fileobj)
                    else:
                        if not block:
                            # Drain any already available error message before reporting EOF.
                            try:
                                self.stderr.extend(os.read(process.stderr.fileno(), 8192))
                                del self.stderr[:-8192]
                            except BlockingIOError:
                                pass
                            raise RuntimeError("NVIDIA 光流辅助程序退出：" + self.stderr[-8192:].decode(errors="replace"))
                        result.extend(block)
                        if count is None and b"\n" in result:
                            if not result.endswith(b"\n") or outgoing:
                                raise ValueError("Invalid NVOF handshake")
                            return bytes(result)
                        if len(result) == limit:
                            if count is None or outgoing:
                                raise ValueError("Oversized NVOF handshake or premature response")
                            return bytes(result)

    def close(self, graceful=False):
        process = self.process
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if graceful:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def probe_nvidia(device=0, *, cancelled=lambda: False) -> dict:
    FlowProvider(kind="nvidia", device=device).validate()
    path = helper_path()
    client = PipeProcess([str(path), "--probe", str(device)], cancelled)
    try:
        report = json.loads(client.exchange(b"", None))
        if report.get("protocol") != 1 or not report.get("grids"):
            raise ValueError("Unsupported NVOF probe response")
        report["helper_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        report["cuda_visible_devices"] = os.environ.get("CUDA_VISIBLE_DEVICES")
        report["host_platform"] = sys.platform
        report["driver_build"] = None
        if sys.platform == "linux":
            driver_file = Path("/proc/driver/nvidia/version")
            report["driver_build"] = driver_file.read_text()[:4096] if driver_file.is_file() else None
        report["status"] = "capability_query_passed"  # Not a claim of successful frame estimation.
        return report
    finally:
        client.close(graceful=True)


def unpack_flow(raw, grid_width, grid_height, width, height):
    """S10.5 pixel displacement -> dense float32, NOT bit-reinterpreted fp16."""
    import cv2
    import numpy as np
    if len(raw) != grid_width * grid_height * 4:
        raise ValueError("Invalid NVOF output plane size")
    flow = np.frombuffer(raw, dtype="<i2").reshape(grid_height, grid_width, 2).astype(np.float32) / 32.0
    # Grid cells describe full input-pixel displacement already: no *grid factor.
    return cv2.resize(flow, (width, height), interpolation=cv2.INTER_LINEAR)


class NvidiaFlow:
    def __init__(self, width, height, config: FlowProvider, *, cancelled=lambda: False):
        config.validate()
        if type(width) is not int or type(height) is not int or not 64 <= width <= 7680 or not 64 <= height <= 4320:
            raise ValueError("Invalid NVIDIA flow dimensions")
        self.width, self.height, self.config = width, height, config
        self.index, self.pending_reset = 0, True
        self.gw, self.gh = ((dimension + config.output_grid - 1) // config.output_grid for dimension in (width, height))
        preset = {"fast": 20, "balanced": 10, "quality": 5}[config.preset]
        self.client = PipeProcess([str(helper_path()), "--serve", str(width), str(height), str(preset),
                                   str(config.output_grid), str(config.device), str(int(config.temporal_hints))], cancelled)
        try:
            ready = json.loads(self.client.exchange(b"", None))
            if ready != {"protocol": 1, "grid_width": self.gw, "grid_height": self.gh}:
                raise ValueError("Unexpected NVOF ready response")
        except BaseException:
            self.client.close()
            raise

    def reset(self):
        self.pending_reset = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def estimate(self, current, previous):
        import numpy as np
        if self.client is None:
            raise RuntimeError("NVIDIA flow session is closed")
        for plane in (previous, current):
            if plane.shape != (self.height, self.width) or plane.dtype != np.uint8:
                raise ValueError("NVOF expects two uint8 grayscale planes of matching size")
        payload = HEADER.pack(MAGIC, self.index, int(self.pending_reset), self.width * self.height)
        payload += previous.tobytes() + current.tobytes()
        size = self.gw * self.gh * 4
        try:
            raw = self.client.exchange(payload, HEADER.size + 2 * size)
            if HEADER.unpack_from(raw) != (MAGIC, self.index, self.gw, self.gh):
                raise ValueError("NVOF response frame mismatch")
            backward = unpack_flow(raw[HEADER.size:HEADER.size + size], self.gw, self.gh, self.width, self.height)
            forward = unpack_flow(raw[HEADER.size + size:], self.gw, self.gh, self.width, self.height)
            self.index += 1
            self.pending_reset = False
            return backward, forward
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.client is not None:
            self.client.close(graceful=True)
            self.client = None
