"""CXR1 camera-aware SR/DLAA/RR client, never a silent CSR1 replacement."""
from __future__ import annotations

import hmac
import math
import socket
import struct
import threading
import time

from .owned_worker import MAX_ERROR_PAYLOAD, ERROR_HEADER, decode_worker_error
from .sl_contract import CameraFrame, SLFrameMetadata, ReconstructionSettings, RRGuides, FRAME_WIRE
from .sr_dimensions import LIMITS

MAGIC, VERSION = 0x31525843, 1
HEADER = struct.Struct('<4I2Q')
CAPABILITIES = struct.Struct('<16I')
MAX_PAYLOAD, MAX_EVALUATIONS = LIMITS['cxr_max_payload'], 1_000_000
CREATE, FRAME, END, RELEASE, SHUTDOWN, PING = range(1, 7)
CAPS, ACK, RESULT, ERROR = range(128, 132)


class ReconstructionClient:
    def __init__(self, connection, token: bytes, *, timeout=120):
        if type(token) is not bytes or len(token) != 32:
            raise ValueError('Expected a 32-byte authentication token')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 600:
            raise ValueError('Invalid reconstruction Worker timeout')
        self.connection, self.timeout = connection, timeout
        self.failed = self.closed = False
        self._lock = threading.RLock()
        self.request_id = self.session_id = self.task_frames = self.session_frames = self.total_frames = 0
        self.settings = self.last_pts = None
        try:
            deadline = time.monotonic() + timeout
            if not hmac.compare_digest(self._read(32, deadline), token):
                raise ValueError('Invalid reconstruction Worker authentication')
            self._write(b'\x01', deadline)
            if HEADER.unpack(self._read(HEADER.size, deadline)) != (MAGIC, VERSION, CAPS, CAPABILITIES.size, 0, 0):
                raise ValueError('Expected CXR1 camera-aware Worker, not CNR1/CSR1')
            caps = CAPABILITIES.unpack(self._read(CAPABILITIES.size, deadline))
            legacy = (64, 64, 1920, 1080, 3840, 2160)
            portrait_v1 = (64, 64, 1920, 1920, 3840, 3840)
            current = (LIMITS['min_side'],)*2+(LIMITS['input_max_side'],)*2+(LIMITS['output_max_side'],)*2
            if caps[:6] not in (legacy,portrait_v1,current) or caps[6:] != (1, 1, 1, 1, MAX_PAYLOAD, 1, 1, 7, 1, MAX_EVALUATIONS):
                raise ValueError('Unsupported reconstruction Worker capability contract')
            self.extent_caps = caps[:6]
            self.output_pixel_cap = LIMITS['output_max_pixels'] if caps[:6] == current else 3840*2160
            self.capabilities = dict(protocol='CXR1', features=['sr', 'dlaa', 'rr'],
                                     compiled=True, device_support='checked_on_create',
                                     max_payload=MAX_PAYLOAD, max_inflight=1, max_sessions=1)
        except BaseException:
            self.failed = True
            self.close()
            raise

    def _read(self, size, deadline):
        if type(size) is not int or not 0 <= size <= MAX_PAYLOAD:
            raise ValueError('Invalid reconstruction read size')
        result = bytearray()
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Reconstruction Worker response timed out')
            self.connection.settimeout(remaining)
            block = self.connection.recv(min(size - len(result), 1024 * 1024))
            if not block:
                raise RuntimeError('Reconstruction Worker disconnected')
            result.extend(block)
        return bytes(result)

    def _write(self, data, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Reconstruction Worker write timed out')
        self.connection.settimeout(remaining)
        self.connection.sendall(data)

    def _exchange(self, kind, session, payload=b'', *, expected=ACK, result_bytes=0):
        if self.closed or self.failed:
            raise RuntimeError('Reconstruction Worker closed/failed')
        if self.request_id >= (1 << 64) - 2:
            raise RuntimeError('Reconstruction request counter exhausted')
        if type(payload) is not bytes or len(payload) > MAX_PAYLOAD or not 0 <= result_bytes <= MAX_PAYLOAD:
            raise ValueError('Reconstruction payload exceeds bounded contract')
        self.request_id += 1
        deadline = time.monotonic() + self.timeout
        try:
            self._write(HEADER.pack(MAGIC, VERSION, kind, len(payload), self.request_id, session), deadline)
            self._write(payload, deadline)
            magic, version, response, size, request, returned_session = HEADER.unpack(self._read(HEADER.size, deadline))
            if (magic, version, request, returned_session) != (MAGIC, VERSION, self.request_id, session):
                raise RuntimeError('Reconstruction response identity mismatch')
            if response == ERROR:
                if not ERROR_HEADER.size <= size <= MAX_ERROR_PAYLOAD:
                    raise RuntimeError('Invalid reconstruction diagnostic size')
                raise decode_worker_error(self._read(size, deadline))
            if response != expected or size != result_bytes:
                raise RuntimeError('Reconstruction response type/size mismatch')
            return self._read(size, deadline)
        except BaseException:
            self.failed = True
            self.close()
            raise

    def create(self, settings: ReconstructionSettings, *, session_id: int):
        if not isinstance(settings, ReconstructionSettings):
            raise ValueError('Expected validated ReconstructionSettings')
        if type(session_id) is not int or not 1 <= session_id < (1 << 64):
            raise ValueError('Invalid reconstruction session ID')
        encoded = settings.encode()
        if (any(actual > maximum for actual,maximum in zip(
                (settings.input_width,settings.input_height,settings.output_width,settings.output_height),self.extent_caps[2:]))
                or settings.output_width*settings.output_height > self.output_pixel_cap):
            raise ValueError('Requested size exceeds this Worker capability; select the matching byte-budget Worker instead of an older binary')
        if settings.frame_bytes > MAX_PAYLOAD or 8+settings.output_width*settings.output_height*8 > MAX_PAYLOAD:
            raise ValueError('Reconstruction frame/result exceeds CXR1 byte budget')
        with self._lock:
            if self.settings is not None:
                raise RuntimeError('Release existing reconstruction session before create')
            self._exchange(CREATE, session_id, encoded)
            self.settings, self.session_id = settings, session_id
            self.task_frames = self.session_frames = 0
            self.last_pts = None

    def process(self, color: bytes, motion: bytes, depth: bytes, pts_ns: int, *,
                camera: CameraFrame, metadata: SLFrameMetadata, rr: RRGuides | None = None):
        with self._lock:
            s = self.settings
            if s is None:
                raise RuntimeError('No reconstruction session')
            if self.session_frames >= MAX_EVALUATIONS:
                raise RuntimeError('Reconstruction session evaluation limit reached')
            pixels = s.input_width * s.input_height
            if any(type(p) is not bytes or len(p) != pixels * stride for p, stride in ((color, 8), (motion, 4), (depth, 4))):
                raise ValueError('Expected packed RGBA16F color, RG16F motion and R32F device depth')
            if type(pts_ns) is not int or not 0 <= pts_ns < (1 << 63) or (self.last_pts is not None and pts_ns <= self.last_pts):
                raise ValueError('Expected strictly increasing non-negative PTS')
            if not isinstance(camera, CameraFrame) or not isinstance(metadata, SLFrameMetadata):
                raise ValueError('Explicit validated camera and frame metadata are required')
            camera.validate_extent(s.input_width, s.input_height)
            metadata.validate_for(s)
            if (s.feature == 'rr' and not isinstance(rr, RRGuides)) or (s.feature == 'sr' and rr is not None):
                raise ValueError('RR requires renderer guides; SR must not silently ignore them')
            payload = FRAME_WIRE.pack(pts_ns, int(metadata.reset or self.task_frames == 0), 0,
                metadata.jitter_x, metadata.jitter_y, metadata.motion_scale_x, metadata.motion_scale_y,
                metadata.pre_exposure, metadata.exposure_scale, metadata.exposure, 0)
            payload += camera.encode() + color + motion + depth
            if rr is not None:
                payload += rr.encode(s)
            result = self._exchange(FRAME, self.session_id, payload, expected=RESULT,
                                    result_bytes=8 + s.output_width * s.output_height * 8)
            if struct.unpack_from('<Q', result)[0] != pts_ns:
                self.failed = True
                self.close()
                raise RuntimeError('Reconstruction Worker changed frame PTS')
            self.task_frames += 1
            self.session_frames += 1
            self.total_frames += 1
            self.last_pts = pts_ns
            return result[8:]

    def end(self):
        with self._lock:
            if self.settings is None or not self.task_frames:
                raise RuntimeError('No completed reconstruction frames to end')
            self._exchange(END, self.session_id)
            self.task_frames, self.last_pts = 0, None

    def release(self):
        with self._lock:
            if self.settings is None:
                raise RuntimeError('No reconstruction session to release')
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
