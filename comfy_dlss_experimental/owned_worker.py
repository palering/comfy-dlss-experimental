"""CNR1 development client for the project-owned Worker; never speaks D5V2.

Transport and NR sessions only. Process launching/platform selection belongs to
the host. This module imports no Linux, CUDA, torch, media or vendor libraries.
"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import math
import socket
import struct
import threading
import time

MAGIC, VERSION = 0x31524E43, 1
HEADER = struct.Struct('<4I2Q')
SETTINGS = struct.Struct('<8I4f')
FRAME = struct.Struct('<QII')
MAX_PAYLOAD = 1920*1080*12+16
CREATE, FRAME_MESSAGE, END, RELEASE, SHUTDOWN, PING = range(1, 7)
CAPS, ACK, RESULT, ERROR = range(128, 132)
ERROR_MAGIC, ERROR_VERSION = 0x31524543, 1  # CER1: optional CNR1 error detail
ERROR_HEADER = struct.Struct('<6I2Q')
MAX_ERROR_PAYLOAD = 256
ERROR_STAGES = ('unknown', 'protocol', 'device', 'load', 'init', 'resources',
                'create', 'create_fence', 'evaluate', 'evaluate_fence',
                'readback', 'release', 'shutdown', 'transport')
ERROR_DOMAINS = ('worker', 'ngx', 'hresult', 'win32', 'winsock', 'wait')


class OwnedWorkerError(RuntimeError):
    """Bounded native diagnostic, safe to include in an execution report.

    Frame/evaluation indices are zero-based; a frame is the next undelivered
    task frame and evaluation includes first-frame warmup. None means legacy
    Worker did not supply structured details, not that the failure was frame 0.
    """
    def __init__(self, *, code, stage='unknown', code_domain='worker',
                 frame_index=None, evaluation_index=None, message='Worker rejected request'):
        self.code, self.stage, self.code_domain = code, stage, code_domain
        self.frame_index, self.evaluation_index, self.message = frame_index, evaluation_index, message
        progress = '' if frame_index is None else f'; frame={frame_index}, evaluation={evaluation_index}'
        super().__init__(f'Owned Worker {stage} failed [{code_domain}=0x{code:08X}{progress}]: {message}')

    def as_dict(self):
        return dict(stage=self.stage, code=self.code, code_hex=f'0x{self.code:08X}',
                    code_domain=self.code_domain, frame_index=self.frame_index,
                    evaluation_index=self.evaluation_index, message=self.message)


def decode_worker_error(payload: bytes):
    """Legacy binaries remain readable; malformed/oversized details fail closed."""
    if len(payload) == 4:
        return OwnedWorkerError(code=struct.unpack('<I', payload)[0])
    if not ERROR_HEADER.size <= len(payload) <= MAX_ERROR_PAYLOAD:
        raise RuntimeError('Invalid owned Worker diagnostic size')
    magic, version, stage, domain, code, size, frame, evaluation = ERROR_HEADER.unpack_from(payload)
    if (magic != ERROR_MAGIC or version != ERROR_VERSION or stage >= len(ERROR_STAGES)
            or domain >= len(ERROR_DOMAINS) or size != len(payload) - ERROR_HEADER.size):
        raise RuntimeError('Invalid owned Worker diagnostic contract')
    # The native side uses fixed ASCII literals. Reject controls/UTF-8 rather
    # than rendering arbitrary terminal escapes or hidden content in reports.
    text = payload[ERROR_HEADER.size:]
    if not text or any(c < 32 or c > 126 for c in text):
        raise RuntimeError('Invalid owned Worker diagnostic message')
    return OwnedWorkerError(code=code, stage=ERROR_STAGES[stage], code_domain=ERROR_DOMAINS[domain],
                            frame_index=frame, evaluation_index=evaluation, message=text.decode('ascii'))


@dataclass(frozen=True)
class OwnedNRSettings:
    width: int
    height: int
    warmup: int = 1
    preset: int = 0
    style: int = 0
    auto_mask: int = 1
    ui_correction: int = 0
    intensity: float = 1.0
    local_tone: float = 1.0
    local_structure: float = 1.0
    skin_structure: float = -1.0

    def encode(self):
        for name, low, high in (('width',64,1920), ('height',64,1080), ('warmup',0,240),
            ('preset',0,3), ('style',0,3), ('auto_mask',0,1), ('ui_correction',0,1)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{name} must be an integer in [{low}, {high}]')
        for name in ('intensity', 'local_tone', 'local_structure', 'skin_structure'):
            value = getattr(self,name)
            if type(value) not in (int,float) or not math.isfinite(value) or not (-1 if name=='skin_structure' else 0) <= value <= 3:
                raise ValueError(f'Invalid {name}')
        return SETTINGS.pack(self.width,self.height,self.warmup,self.preset,self.style,self.auto_mask,self.ui_correction,0,
            self.intensity,self.local_tone,self.local_structure,self.skin_structure)


class OwnedNRClient:
    """Single ordered connection; END retains the session, RELEASE drops GPU state.

    One request in flight. Closing the socket cancels future transport, not GPU
    kernels already dispatched. The owning process manager must reap the Worker.
    """
    def __init__(self, connection, token: bytes, *, timeout=120):
        if type(token) is not bytes or len(token)!=32:
            raise ValueError('Expected a 32-byte authentication token')
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 1<=timeout<=600:
            raise ValueError('Invalid Worker timeout')
        self.connection, self.timeout = connection, timeout
        self.failed = False
        self.closed = False
        self._lock = threading.RLock()
        self.request_id = 0
        self.session_id = 0
        self.settings = None
        self.task_frames = 0
        self.total_frames = 0
        self.last_pts = None
        try:
            deadline = time.monotonic()+timeout
            if not hmac.compare_digest(self._read(32,deadline), token):
                raise ValueError('Invalid owned Worker authentication')
            self._write(b'\x01',deadline)
            magic,version,kind,size,request,session = HEADER.unpack(self._read(HEADER.size,deadline))
            if (magic,version,kind,size,request,session)!=(MAGIC,VERSION,CAPS,32,0,0):
                raise ValueError('Invalid owned Worker capabilities header')
            caps = struct.unpack('<8I',self._read(size,deadline))
            if caps!=(64,64,1920,1080,1,1,MAX_PAYLOAD,1):
                raise ValueError('Unsupported owned Worker capability contract')
            self.capabilities = dict(protocol='CNR1',feature='nr',color='rgba16f_le',motion='rg16f_le',max_sessions=1,
                                     maximum_width=1920,maximum_height=1080,max_payload=MAX_PAYLOAD)
        except BaseException:
            self.failed = True; self.close(); raise

    def _read(self, size, deadline):
        if not 0<=size<=MAX_PAYLOAD: raise ValueError('Invalid read size')
        result = bytearray()
        while len(result)<size:
            remaining = deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('Owned Worker response timed out')
            self.connection.settimeout(remaining)
            block = self.connection.recv(min(size-len(result),1024*1024))
            if not block: raise RuntimeError('Owned Worker disconnected')
            result.extend(block)
        return bytes(result)

    def _write(self, data, deadline):
        remaining = deadline-time.monotonic()
        if remaining<=0: raise TimeoutError('Owned Worker write timed out')
        self.connection.settimeout(remaining)
        self.connection.sendall(data)

    def _exchange(self, kind, session, payload=b'', *, expected=ACK, result_bytes=0):
        if self.closed or self.failed: raise RuntimeError('Owned Worker connection closed/failed')
        if self.request_id >= (1<<64)-2: raise RuntimeError('Owned Worker request counter exhausted')
        self.request_id += 1
        deadline = time.monotonic()+self.timeout
        try:
            self._write(HEADER.pack(MAGIC,VERSION,kind,len(payload),self.request_id,session),deadline)
            self._write(payload,deadline)
            magic,version,response,size,request,returned_session = HEADER.unpack(self._read(HEADER.size,deadline))
            if (magic,version,request,returned_session)!=(MAGIC,VERSION,self.request_id,session):
                raise RuntimeError('Owned Worker response identity mismatch')
            if response==ERROR:
                if size != 4 and not ERROR_HEADER.size <= size <= MAX_ERROR_PAYLOAD:
                    raise RuntimeError('Invalid owned Worker diagnostic size')
                raise decode_worker_error(self._read(size,deadline))
            if response!=expected or size!=result_bytes:
                raise RuntimeError('Owned Worker response type/size mismatch')
            return self._read(size,deadline)
        except BaseException:
            self.failed = True; self.close(); raise

    def create(self, settings: OwnedNRSettings, *, session_id: int):
        if not isinstance(settings,OwnedNRSettings): raise ValueError('Expected owned NR settings')
        encoded = settings.encode()
        if type(session_id) is not int or not 1<=session_id<(1<<64): raise ValueError('Invalid session ID')
        with self._lock:
            if self.settings is not None: raise RuntimeError('Release existing session before create')
            self._exchange(CREATE,session_id,encoded)
            self.settings,self.session_id = settings,session_id
            self.task_frames,self.last_pts = 0,None

    def process(self, color: bytes, motion: bytes, pts_ns: int, *, reset: bool = False):
        with self._lock:
            if self.settings is None: raise RuntimeError('No owned NR session')
            pixels=self.settings.width*self.settings.height
            if type(color) is not bytes or type(motion) is not bytes or len(color)!=pixels*8 or len(motion)!=pixels*4:
                raise ValueError('Expected tightly packed RGBA16F color and RG16F motion')
            if type(pts_ns) is not int or not 0<=pts_ns<(1<<63) or (self.last_pts is not None and pts_ns<=self.last_pts):
                raise ValueError('Expected increasing non-negative PTS within a task')
            if type(reset) is not bool: raise ValueError('Reset must be boolean')
            payload=FRAME.pack(pts_ns,int(reset or self.task_frames==0),0)+color+motion
            result=self._exchange(FRAME_MESSAGE,self.session_id,payload,expected=RESULT,result_bytes=8+pixels*8)
            pts,=struct.unpack_from('<Q',result)
            if pts!=pts_ns:
                self.failed=True; self.close(); raise RuntimeError('Owned Worker changed frame PTS')
            self.task_frames+=1; self.total_frames+=1; self.last_pts=pts_ns
            return result[8:]

    def end(self):
        with self._lock:
            if self.settings is None or not self.task_frames: raise RuntimeError('No completed frames to end')
            self._exchange(END,self.session_id)
            self.task_frames,self.last_pts=0,None

    def release(self):
        with self._lock:
            if self.settings is None: raise RuntimeError('No session to release')
            self._exchange(RELEASE,self.session_id)
            self.settings,self.session_id=None,0
            self.task_frames,self.last_pts=0,None

    def ping(self):
        with self._lock: self._exchange(PING,0)

    def shutdown(self):
        with self._lock:
            self._exchange(SHUTDOWN,0)
            self.settings,self.session_id=None,0
            self.close()

    def close(self):
        if not self.closed:
            self.closed=True
            try: self.connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            finally: self.connection.close()
