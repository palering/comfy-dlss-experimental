"""Own CNR1/CSR1/CXR1 Worker process; no external relay or system service required.

Development backend, not selected by legacy presets. OS-specific ownership is
loaded only after validating the actual host. A dedicated Proton prefix is required.
"""
from __future__ import annotations

import contextlib
import math
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading

from .owned_worker import OwnedNRClient

MAX_LOG = 4*1024*1024


def launch_platform(proton, compatdata):
    if sys.platform == 'win32':
        if proton is not None or compatdata is not None:
            raise ValueError('Native Windows Worker must not use Proton/prefix settings')
        return 'windows'
    if not sys.platform.startswith('linux'):
        raise ValueError('Owned Worker execution requires Windows or Linux; no remote execution is implied')
    if proton is None or compatdata is None or not Path(proton).is_file():
        raise ValueError('Linux Worker requires an existing Proton executable and dedicated prefix')
    if not hasattr(os, 'pidfd_open'):
        raise RuntimeError('Linux pidfd process ownership support is required')
    return 'linux'


class OwnedWorkerProcess:
    def __init__(self, worker: Path, model: Path, caller: Path | None, job_dir: Path, *,
                 proton: Path | None = None, compatdata: Path | None = None,
                 environment: dict[str,str] | None = None, timeout=120,
                 feature: str = 'nr', project_id: str | None = None):
        if feature not in ('nr', 'sr', 'sl'):
            raise ValueError('Owned Worker feature must be nr, sr or sl; no automatic fallback')
        self.feature = feature
        if feature in ('sr', 'sl'):
            if caller is not None:
                raise ValueError('Reconstruction uses the official SDK, not the NR caller shim')
            from .sr_runtime import project_id as validate_project_id
            validate_project_id(project_id)
        elif project_id is not None:
            raise ValueError('NGX project_id is only used by the SR backend')
        self.platform = launch_platform(proton,compatdata)
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 1<=timeout<=600:
            raise ValueError('Invalid owned Worker timeout')
        worker,model=(Path(p).resolve(strict=True) for p in (worker,model))
        if feature == 'nr':
            if caller is None:
                raise ValueError('NR requires a thin caller shim')
            caller = Path(caller).resolve(strict=True)
            if not all(p.is_file() for p in (worker,model,caller)):
                raise ValueError('Worker, NR model and thin caller must be regular files')
        elif feature == 'sl':
            required = ('sl.interposer.dll', 'sl.common.dll', 'NvLowLatencyVk.dll',
                        'sl.dlss.dll', 'nvngx_dlss.dll', 'sl.dlss_d.dll', 'nvngx_dlssd.dll')
            if not worker.is_file() or not model.is_dir() or not all((model / name).is_file() for name in required):
                raise ValueError('SL requires an explicit Worker and complete SR/RR runtime directory')
        elif (not worker.is_file() or not model.is_dir()
              or not (model / 'nvngx_dlss.dll').is_file()):
            raise ValueError('SR requires a Worker file and a model directory containing nvngx_dlss.dll')
        self.client = None
        self._process = self._socket = self._lock_file = self._drain_thread = self._log = None
        self._closed = False
        self._cleanup_complete = False
        self.cleanup = {}
        self.run_marker = secrets.token_hex(16)
        self.job_dir = Path(job_dir).resolve()
        self.job_dir.mkdir(parents=True,exist_ok=False)
        try:
            self._log=(self.job_dir/'worker-launcher.log').open('xb')
            logs=self.job_dir/'ngx';logs.mkdir()
            env=dict(os.environ)
            if environment:env.update(environment)
            if self.platform=='linux':
                import fcntl
                from .native_relay import RUN_MARKER
                prefix=Path(compatdata).resolve();prefix.mkdir(parents=True,exist_ok=True)
                self._lock_file=(prefix/'comfy-owned.lock').open('ab')
                fcntl.flock(self._lock_file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                env[RUN_MARKER]=self.run_marker
                env.update(STEAM_COMPAT_DATA_PATH=str(prefix),SteamGameId='0',PROTON_LOG_DIR=str(self.job_dir))
                # No runinprefix or manually substituted DXVK overrides.
                command=[str(Path(proton).resolve()),'run',str(worker)]
                path=lambda p:'Z:'+str(p).replace('/','\\')
            else:
                command=[str(worker)]
                path=str
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
                listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(min(timeout,45))
                token=secrets.token_bytes(32)
                if feature == 'nr':
                    command += ['--serve-nr',path(model),path(caller),path(logs),
                                str(listener.getsockname()[1]),token.hex()]
                else:
                    command += ['--serve-sl' if feature == 'sl' else '--serve-sr',path(model),path(logs),project_id,
                                str(listener.getsockname()[1]),token.hex()]
                self._process=subprocess.Popen(command,cwd=self.job_dir,env=env,stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=self.platform=='linux')
                self._drain_thread=threading.Thread(target=self._drain,name='dlss-owned-log',daemon=True)
                self._drain_thread.start()
                self._socket,peer=listener.accept()
                if peer[0]!='127.0.0.1':raise RuntimeError('Non-loopback Worker peer')
                self._socket.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                if feature == 'nr':
                    self.client=OwnedNRClient(self._socket,token,timeout=timeout)
                elif feature == 'sr':
                    from .sr_worker import OwnedSRClient
                    self.client=OwnedSRClient(self._socket,token,timeout=timeout)
                else:
                    from .sl_worker import ReconstructionClient
                    self.client=ReconstructionClient(self._socket,token,timeout=timeout)
        except BaseException:
            self.close();raise

    def _drain(self):
        written=0
        try:
            while True:
                chunk=self._process.stdout.read(65536)
                if not chunk:return
                available=max(0,MAX_LOG-written)
                if available:
                    self._log.write(chunk[:available]);self._log.flush();written+=min(len(chunk),available)
        except (OSError,ValueError):
            # Log failures must not leave an undrained pipe blocking native code.
            if self._process is not None and self._process.stdout is not None:
                with contextlib.suppress(OSError,ValueError):
                    while self._process.stdout.read(65536):pass

    @property
    def healthy(self):
        # Proton wrapper exit is NOT Worker health/completion.
        return not self._closed and self.client is not None and not self.client.closed and not self.client.failed

    def cancel(self):
        if self.client is not None:self.client.close()
        elif self._socket is not None:
            with contextlib.suppress(OSError):self._socket.shutdown(socket.SHUT_RDWR)
            self._socket.close()

    def shutdown(self):
        try:
            if self.healthy:self.client.shutdown()
        finally:self.close()

    def close(self):
        if self._cleanup_complete:return
        self._closed=True
        self.cancel()
        error=None
        try:
            if self.platform=='linux' and self._process is not None:
                # Reuse the existing tested ownership primitive, not relay execution.
                from .native_relay import _reap_marked_processes
                self.cleanup=_reap_marked_processes(self.run_marker)
            elif self._process is not None:
                try:self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:self._process.kill()
            if self._process is not None:
                try:self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:self._process.kill();self._process.wait(timeout=5)
        except BaseException as exc:error=exc
        finally:
            if self._drain_thread is not None:self._drain_thread.join(timeout=3)
            if self._drain_thread is not None and self._drain_thread.is_alive():
                error=error or RuntimeError('Owned Worker log drain did not stop')
            else:
                if self._process is not None and self._process.stdout is not None:self._process.stdout.close()
                if self._log is not None:self._log.close()
            if error is None:
                if self._lock_file is not None:self._lock_file.close()
                self._cleanup_complete=True
        if error is not None:raise error

    def __enter__(self):return self
    def __exit__(self,*_):self.close()
