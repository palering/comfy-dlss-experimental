"""Bounded CSR1 transport for SDK-enabled SR/DLAA Workers.

This module does not load a platform, GPU, media or vendor library. Plane byte
layout is checked here; numeric plane contents are checked again by the native
SR engine before upload. SR never falls back to CNR1 or uses an NR caller shim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import math
import socket
import struct
import threading
import time

from .owned_worker import MAX_ERROR_PAYLOAD, ERROR_HEADER, decode_worker_error
from .sr_contract import SRFrameMetadata, SRSettings, plan_sr

MAGIC, VERSION = 0x31525343, 1  # CSR1
HEADER = struct.Struct('<4I2Q')
SETTINGS = struct.Struct('<8I')
FRAME = struct.Struct('<QII8f')
CAPABILITIES = struct.Struct('<16I')
MAX_PAYLOAD = 8 + 3840 * 2160 * 8
MAX_EVALUATIONS = 1_000_000
CREATE, FRAME_MESSAGE, END, RELEASE, SHUTDOWN, PING = range(1, 7)
CAPS, ACK, RESULT, ERROR = range(128, 132)


@dataclass(frozen=True)
class OwnedSRSettings:
    input_width: int
    input_height: int
    output_width: int
    output_height: int
    settings: SRSettings = field(default_factory=SRSettings)

    def __post_init__(self):
        # Freeze a payload copy into the validated value object. Later edits to
        # an upstream dictionary must not change a live native session contract.
        object.__setattr__(self, 'settings', SRSettings.from_payload(self.settings))

    def encode(self):
        config = SRSettings.from_payload(self.settings)
        plan = plan_sr(config, self.input_width, self.input_height,
                       self.output_width, self.output_height)
        native = plan['native_settings']
        flags = (int(config.hdr) | (int(config.depth_inverted) << 1)
                 | (int(config.motion_jittered) << 2) | (int(config.auto_exposure) << 3))
        return SETTINGS.pack(self.input_width, self.input_height,
                             self.output_width, self.output_height,
                             native['mode'], native['preset'], flags, 0)

    @classmethod
    def from_plan(cls, report):
        """Use original settings/extents, not untrusted cached native mappings."""
        if not isinstance(report, dict) or report.get('kind') != 'sr_input_plan':
            raise ValueError('Expected an SR input plan')
        try:
            result = cls(report['input']['width'], report['input']['height'],
                         report['output']['width'], report['output']['height'],
                         SRSettings.from_payload(report['settings']))
        except (KeyError, TypeError) as exc:
            raise ValueError('Incomplete SR input plan') from exc
        result.encode()
        return result


class OwnedSRClient:
    """One ordered request in flight, one GPU session, bounded frames and memory.

    END restarts task PTS/history but retains the engine and session evaluation
    count. RELEASE drops the engine; a later CREATE begins a new session. Closing
    transport does not cancel an already dispatched GPU command: the process
    owner remains responsible for cancellation and child-process cleanup.
    """
    def __init__(self, connection, token: bytes, *, timeout=120):
        if type(token) is not bytes or len(token) != 32:
            raise ValueError('Expected a 32-byte authentication token')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 600:
            raise ValueError('Invalid Worker timeout')
        self.connection, self.timeout = connection, timeout
        self.failed = self.closed = False
        self._lock = threading.RLock()
        self.request_id = self.session_id = 0
        self.task_frames = self.total_frames = self.session_frames = 0
        self.settings = self.last_pts = None
        try:
            deadline = time.monotonic() + timeout
            if not hmac.compare_digest(self._read(32, deadline), token):
                raise ValueError('Invalid SR Worker authentication')
            self._write(b'\x01', deadline)
            header = HEADER.unpack(self._read(HEADER.size, deadline))
            if header != (MAGIC, VERSION, CAPS, CAPABILITIES.size, 0, 0):
                raise ValueError('Expected CSR1 capabilities; install an SR-enabled Worker, not an NR-only binary')
            caps = CAPABILITIES.unpack(self._read(CAPABILITIES.size, deadline))
            expected = (64, 64, 1920, 1080, 3840, 2160, 1, 1, 1, 1,
                        MAX_PAYLOAD, 1, 1, 3)
            if caps[:14] != expected or caps[14] not in (0, 1) or caps[15] != MAX_EVALUATIONS:
                raise ValueError('Unsupported SR Worker capability contract')
            if not caps[14]:
                raise ValueError('Worker has no linked SR SDK; install an SR-enabled Worker build')
            self.capabilities = dict(protocol='CSR1', feature='sr', features=['sr', 'dlaa'],
                sdk_compiled=True, color='rgba16f_le', motion='rg16f_le', depth='r32f_le',
                output='rgba16f_le', maximum_width=1920, maximum_height=1080,
                maximum_output_width=3840, maximum_output_height=2160,
                max_payload=MAX_PAYLOAD, max_sessions=1, max_inflight=1,
                max_evaluations=MAX_EVALUATIONS)
        except BaseException:
            self.failed = True
            self.close()
            raise

    def _read(self, size, deadline):
        if type(size) is not int or not 0 <= size <= MAX_PAYLOAD:
            raise ValueError('Invalid SR read size')
        result = bytearray()
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('SR Worker response timed out')
            self.connection.settimeout(remaining)
            block = self.connection.recv(min(size - len(result), 1024 * 1024))
            if not block:
                raise RuntimeError('SR Worker disconnected')
            result.extend(block)
        return bytes(result)

    def _write(self, data, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('SR Worker write timed out')
        self.connection.settimeout(remaining)
        self.connection.sendall(data)

    def _exchange(self, kind, session, payload=b'', *, expected=ACK, result_bytes=0):
        if self.closed or self.failed:
            raise RuntimeError('SR Worker connection closed/failed')
        if self.request_id >= (1 << 64) - 2:
            raise RuntimeError('SR Worker request counter exhausted')
        if type(payload) is not bytes or len(payload) > MAX_PAYLOAD or not 0 <= result_bytes <= MAX_PAYLOAD:
            raise ValueError('SR request/result exceeds bounded wire contract')
        self.request_id += 1
        deadline = time.monotonic() + self.timeout
        try:
            self._write(HEADER.pack(MAGIC, VERSION, kind, len(payload), self.request_id, session), deadline)
            self._write(payload, deadline)
            magic, version, response, size, request, returned_session = HEADER.unpack(self._read(HEADER.size, deadline))
            if (magic, version, request, returned_session) != (MAGIC, VERSION, self.request_id, session):
                raise RuntimeError('SR Worker response identity mismatch')
            if response == ERROR:
                if size != 4 and not ERROR_HEADER.size <= size <= MAX_ERROR_PAYLOAD:
                    raise RuntimeError('Invalid SR Worker diagnostic size')
                raise decode_worker_error(self._read(size, deadline))
            if response != expected or size != result_bytes:
                raise RuntimeError('SR Worker response type/size mismatch')
            return self._read(size, deadline)
        except BaseException:
            self.failed = True
            self.close()
            raise

    def create(self, settings: OwnedSRSettings, *, session_id: int):
        if not isinstance(settings, OwnedSRSettings):
            raise ValueError('Expected owned SR settings')
        encoded = settings.encode()
        # Shared planning accepts portrait, but this legacy CSR1 handshake does
        # not advertise the new orientation-neutral CXR1 bounds.
        caps = self.capabilities
        if (settings.input_width > caps['maximum_width'] or settings.input_height > caps['maximum_height'] or
                settings.output_width > caps['maximum_output_width'] or settings.output_height > caps['maximum_output_height']):
            raise ValueError('Requested dimensions exceed the connected legacy CSR1 Worker capabilities')
        if type(session_id) is not int or not 1 <= session_id < (1 << 64):
            raise ValueError('Invalid SR session ID')
        with self._lock:
            if self.settings is not None:
                raise RuntimeError('Release existing SR session before create')
            self._exchange(CREATE, session_id, encoded)
            self.settings, self.session_id = settings, session_id
            self.task_frames = self.session_frames = 0
            self.last_pts = None

    def process(self, color: bytes, motion: bytes, depth: bytes, pts_ns: int, *,
                metadata: SRFrameMetadata | None = None):
        with self._lock:
            if self.settings is None:
                raise RuntimeError('No owned SR session')
            if self.session_frames >= MAX_EVALUATIONS:
                raise RuntimeError('SR session evaluation limit reached; release and create a new session')
            pixels = self.settings.input_width * self.settings.input_height
            if any(type(plane) is not bytes or len(plane) != pixels * stride
                   for plane, stride in ((color, 8), (motion, 4), (depth, 4))):
                raise ValueError('Expected tightly packed RGBA16F color, RG16F motion and R32F device depth')
            if (type(pts_ns) is not int or not 0 <= pts_ns < (1 << 63)
                    or (self.last_pts is not None and pts_ns <= self.last_pts)):
                raise ValueError('Expected increasing non-negative PTS within an SR task')
            config = SRSettings.from_payload(self.settings.settings)
            if metadata is None:
                if not config.auto_exposure or config.jitter_policy != 'unjittered_video':
                    raise ValueError('SR requires explicit exposure/jitter frame metadata for these settings')
                metadata = SRFrameMetadata()
            if not isinstance(metadata, SRFrameMetadata):
                raise ValueError('Expected validated SRFrameMetadata')
            metadata.validate_for(config)
            payload = FRAME.pack(pts_ns, int(metadata.reset or self.task_frames == 0), 0,
                metadata.jitter_x, metadata.jitter_y, metadata.motion_scale_x, metadata.motion_scale_y,
                metadata.exposure, metadata.pre_exposure, metadata.exposure_scale, metadata.frame_time_ms)
            # Positive Python floats can underflow to zero in binary32. Reject
            # the actual wire representation before sending any frame bytes.
            values = FRAME.unpack(payload)
            SRFrameMetadata(*values[3:], reset=bool(values[1])).validate_for(config)
            payload += color + motion + depth
            output_bytes = self.settings.output_width * self.settings.output_height * 8
            result = self._exchange(FRAME_MESSAGE, self.session_id, payload,
                                    expected=RESULT, result_bytes=8 + output_bytes)
            if struct.unpack_from('<Q', result)[0] != pts_ns:
                self.failed = True
                self.close()
                raise RuntimeError('SR Worker changed frame PTS')
            self.task_frames += 1
            self.total_frames += 1
            self.session_frames += 1
            self.last_pts = pts_ns
            return result[8:]

    def end(self):
        with self._lock:
            if self.settings is None or not self.task_frames:
                raise RuntimeError('No completed SR frames to end')
            self._exchange(END, self.session_id)
            self.task_frames, self.last_pts = 0, None

    def release(self):
        with self._lock:
            if self.settings is None:
                raise RuntimeError('No SR session to release')
            self._exchange(RELEASE, self.session_id)
            self.settings, self.session_id = None, 0
            self.task_frames = self.session_frames = 0
            self.last_pts = None

    def ping(self):
        with self._lock:
            self._exchange(PING, 0)

    def shutdown(self):
        with self._lock:
            self._exchange(SHUTDOWN, 0)
            self.settings, self.session_id = None, 0
            self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                self.connection.close()
