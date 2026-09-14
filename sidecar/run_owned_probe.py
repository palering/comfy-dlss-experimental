"""GPU-free owned Worker acceptance using the established Proton run path.

This diagnostic runner is not a production backend. Requires an existing,
dedicated Proton prefix. Worker completion comes from authenticated loopback
messages, NEVER from the Proton wrapper's exit code or stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

STAGES = {'process', 'parameter_abi', 'device', 'adapter_vendor', 'device_create',
          'model_load', 'init', 'resources', 'create', 'create_fence', 'evaluate',
          'evaluate_fence', 'frame', 'release', 'shutdown'}
STATES = {'ready', 'begin', 'returned', 'observed', 'done', 'failed', 'complete'}


def receive_exact(connection, count, deadline):
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Worker report deadline exceeded')
        connection.settimeout(remaining)
        block = connection.recv(count - len(result))
        if not block:
            raise RuntimeError('Worker disconnected before completion')
        result.extend(block)
    return bytes(result)


def validate_event(value, sequence):
    if not isinstance(value, dict) or set(value) != {'protocol', 'sequence', 'stage', 'state', 'code'}:
        raise ValueError('Invalid report fields')
    for key in ('protocol', 'sequence', 'code'):
        if type(value[key]) is not int:
            raise ValueError('Report integers must not be booleans')
    if value['protocol'] != 1 or value['sequence'] != sequence or not 0 <= value['code'] <= 0xFFFFFFFF:
        raise ValueError('Invalid report protocol, order or code')
    if type(value['stage']) is not str or type(value['state']) is not str or value['stage'] not in STAGES or value['state'] not in STATES:
        raise ValueError('Unknown diagnostic stage/state')
    if sequence == 0 and (value['stage'], value['state'], value['code']) != ('process', 'ready', 0):
        raise ValueError('Missing Worker ready handshake')
    if sequence > 0 and value['state'] == 'ready':
        raise ValueError('Duplicate ready handshake')
    if value['state'] == 'complete' and value['stage'] != 'process':
        raise ValueError('Only process may complete')
    return value


def collect_reports(connection, token, deadline, events, log):
    if not hmac.compare_digest(receive_exact(connection, 32, deadline), token):
        raise ValueError('Invalid Worker authentication')
    connection.sendall(b'\x01')
    for sequence in range(4096):
        line = bytearray()
        while len(line) < 512:
            byte = receive_exact(connection, 1, deadline)
            if byte == b'\n':
                break
            line.extend(byte)
        else:
            raise ValueError('Worker report exceeds size limit')
        event = validate_event(json.loads(line), sequence)
        events.append(event)
        log.write(json.dumps(event) + '\n')
        log.flush()
        if event['state'] == 'complete':
            connection.sendall(b'\x01')
            return event['code']
    raise ValueError('Too many Worker reports')


def abi_passed(events, completed_code):
    return completed_code == 0 and any(
        event['stage'] == 'parameter_abi' and event['state'] == 'returned' and event['code'] == 0
        for event in events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', required=True, type=Path)
    parser.add_argument('--proton', required=True, type=Path)
    parser.add_argument('--prefix', required=True, type=Path, help='Existing isolated compatdata directory; never daily/shared')
    parser.add_argument('--steam', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New per-attempt directory, not overwritten')
    parser.add_argument('--mode', choices=('abi', 'report-failure'), default='abi')
    args = parser.parse_args()
    if os.name != 'posix' or not Path('/proc').is_dir():
        parser.error('This acceptance runner targets Linux/Proton only')
    import fcntl
    worker, proton, prefix, steam = (p.resolve() for p in (args.worker, args.proton, args.prefix, args.steam))
    server = proton.parent / 'files/bin/wineserver'
    if not all(p.is_file() for p in (worker, proton, server, prefix / 'version')) or not (prefix / 'pfx').is_dir() or not steam.is_dir():
        parser.error('Worker, Proton, Steam or initialized prefix is missing')
    output = args.output.resolve()
    output.mkdir(parents=False, exist_ok=False)
    environment = dict(os.environ)
    environment.update(STEAM_COMPAT_DATA_PATH=str(prefix), STEAM_COMPAT_CLIENT_INSTALL_PATH=str(steam),
                       SteamGameId='0', PROTON_LOG='1', PROTON_LOG_DIR=str(output), PROTONFIXES_DISABLE='1',
                       WINEDLLOVERRIDES='', WINEDEBUG='-all',
                       XDG_CACHE_HOME=str(output / 'cache'), TMPDIR=str(output / 'tmp'))
    for name in ('cache', 'tmp'):
        (output / name).mkdir()
    # Lock held through cleanup. No GPU function or model DLL can be selected by this CLI.
    events, error, completed_code = [], None, None
    cleanup_codes = []
    started = time.monotonic()
    process = None
    with (prefix / 'owned-probe.lock').open('ab') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener, (output / 'launcher.log').open('xb') as launch_log, (output / 'events.jsonl').open('x') as event_log:
                listener.bind(('127.0.0.1', 0)); listener.listen(1)
                token = secrets.token_bytes(32)
                flag = '--self-test-parameter-abi' if args.mode == 'abi' else '--self-test-report-failure'
                command = [str(proton), 'run', str(worker), flag, '--report', str(listener.getsockname()[1]), token.hex()]
                process = subprocess.Popen(command, cwd=output, env=environment, stdout=launch_log, stderr=launch_log, start_new_session=True)
                deadline = time.monotonic() + 45
                listener.settimeout(45)
                connection, peer = listener.accept()
                with connection:
                    if peer[0] != '127.0.0.1':
                        raise ValueError('Non-loopback Worker peer')
                    completed_code = collect_reports(connection, token, deadline, events, event_log)
        except (OSError, ValueError, RuntimeError) as exc:
            error = f'{type(exc).__name__}: {exc}'
        finally:
            if process is not None:
                # Completion or deadline, NOT launcher.poll(), controls prefix cleanup.
                cleanup_env = dict(environment, WINEPREFIX=str(prefix / 'pfx'))
                for option in ('-k', '-w'):
                    try:
                        status = subprocess.run([str(server), option], env=cleanup_env, capture_output=True, timeout=10)
                        cleanup_codes.append(status.returncode)
                    except subprocess.TimeoutExpired:
                        cleanup_codes.append(124)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=5)
    passed = error is None and abi_passed(events, completed_code) and cleanup_codes == [0, 0]
    result = dict(mode=args.mode, abi_verified=passed, worker_completed=completed_code is not None,
                  worker_code=completed_code, launcher_code=process.returncode if process else None,
                  error=error, cleanup_codes=cleanup_codes, elapsed_seconds=round(time.monotonic()-started, 3),
                  worker_sha256=hashlib.sha256(worker.read_bytes()).hexdigest(), gpu_started=False,
                  last_event=events[-1] if events else None)
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
