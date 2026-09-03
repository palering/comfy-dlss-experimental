"""Bounded blocking-pipe transport for Windows (select() cannot poll its pipes).

No Windows imports or handles are created on module import. Reader threads also
work on POSIX, allowing failure/backpressure tests without a Windows GPU.
"""
import queue
import subprocess
import threading
import time


class WindowsPipeProcess:
    def __init__(self, command, cancelled=lambda: False, timeout=30):
        self.cancelled, self.timeout = cancelled, timeout
        self.stderr = bytearray()
        self.pending = bytearray()
        self.output = queue.Queue(maxsize=8)
        self.stop = threading.Event()
        self.closed = False
        self.writer = None
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=0, close_fds=True)
        self.readers = [threading.Thread(target=self._read_output, daemon=True),
                        threading.Thread(target=self._read_error, daemon=True)]
        try:
            for thread in self.readers:
                thread.start()
        except BaseException:
            self.close()
            raise

    def _put(self, value):
        while not self.stop.is_set():
            try:
                self.output.put(value, timeout=.05)
                return
            except queue.Full:
                pass

    def _read_output(self):
        try:
            while not self.stop.is_set():
                block = self.process.stdout.read(65536)
                if not block:
                    break
                self._put(block)
        finally:
            self._put(None)

    def _read_error(self):
        while not self.stop.is_set():
            block = self.process.stderr.read(4096)
            if not block:
                break
            self.stderr.extend(block)
            del self.stderr[:-8192]

    def exchange(self, payload, count):
        if self.closed or (self.writer is not None and self.writer.is_alive()):
            raise RuntimeError("NVOF pipe is closed or busy")
        deadline = time.monotonic() + self.timeout
        written = threading.Event()
        failures = []
        def write():
            try:
                outgoing = memoryview(payload)
                while outgoing and not self.stop.is_set():
                    size = self.process.stdin.write(outgoing[:65536])
                    if not size:
                        raise BrokenPipeError("NVOF closed input")
                    outgoing = outgoing[size:]
            except (OSError, ValueError) as error:
                failures.append(error)
            finally:
                written.set()
        self.writer = threading.Thread(target=write, daemon=True)
        self.writer.start()
        limit = 4096 if count is None else count
        result = bytearray()
        complete = False
        while True:
            if self.cancelled():
                raise InterruptedError("NVIDIA 光流计算已取消")
            if time.monotonic() >= deadline:
                raise TimeoutError("NVIDIA 光流辅助程序超时")
            if failures:
                raise RuntimeError("NVIDIA 光流辅助程序关闭了输入管道") from failures[0]
            if complete and written.is_set():
                self.writer.join()
                return bytes(result)
            if complete:
                written.wait(.01)
                continue
            if not self.pending:
                try:
                    block = self.output.get(timeout=.05)
                except queue.Empty:
                    continue
                if block is None:
                    self.readers[1].join(.1)
                    raise RuntimeError("NVIDIA 光流辅助程序退出：" + self.stderr[-8192:].decode(errors="replace"))
                self.pending.extend(block)
            size = min(limit - len(result), len(self.pending))
            result.extend(self.pending[:size])
            del self.pending[:size]
            if count is None and b"\n" in result:
                if not result.endswith(b"\n") or self.pending:
                    raise ValueError("Invalid NVOF handshake")
                complete = True
            elif len(result) == limit:
                if count is None:
                    raise ValueError("Oversized NVOF handshake")
                complete = True

    def close(self, graceful=False):
        if self.closed:
            return
        # Never close a pipe from another thread while its write can be blocked.
        if graceful and (self.writer is None or not self.writer.is_alive()):
            self.process.stdin.close()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        self.stop.set()
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        for thread in [*self.readers, self.writer]:
            if thread is not None and thread.ident is not None:
                thread.join(5)
                if thread.is_alive():
                    raise RuntimeError("NVOF pipe thread did not terminate")
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            pipe.close()
        self.closed = True
