#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comfy_dlss_experimental.jobs import augment_graphical_session, stage_runtime_job
from comfy_dlss_experimental.models import ProtonInstallation
from comfy_dlss_experimental.presets import RuntimePreset, execution_fingerprint, preset_fingerprint
from comfy_dlss_experimental.runtime import build_launch_plan
from comfy_dlss_experimental.sidecar_client import SidecarClient
from comfy_dlss_experimental.sidecar_protocol import ProtocolError
from scripts.frame_smoke_cases import smoke_frames, verify_readback


def existing_file(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file not found: {path}")
    return path


def terminate_smoke_processes(process: subprocess.Popen, marker: str) -> None:
    """Kill only this launch group and Linux children with our exact marker."""
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        proc_root = Path("/proc")
        if proc_root.is_dir():
            for entry in proc_root.iterdir():
                if not entry.name.isdigit():
                    continue
                descriptor = None
                try:
                    if entry.stat().st_uid != os.getuid():
                        continue
                    # Pin process identity before checking its environment.
                    descriptor = os.pidfd_open(int(entry.name))
                    environment = (entry / "environ").read_bytes().split(bytes(1))
                    if f"COMFY_DLSS_SMOKE_ID={marker}".encode() in environment:
                        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except (OSError, AttributeError):
                    continue
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
    elif process.poll() is None:
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic multi-format D3D12 frame round-trips.")
    parser.add_argument("--preset", type=existing_file, required=True)
    parser.add_argument("--proton", type=existing_file)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--presents", type=int, default=60)
    parser.add_argument("--display-backend", choices=("xwayland", "wayland_native_experimental"), default="xwayland")
    arguments = parser.parse_args()

    preset = RuntimePreset.load(arguments.preset)
    preset_key, _ = preset_fingerprint(preset, base_dir=arguments.preset.parent)
    platform_name = "linux" if arguments.proton else "windows"
    proton = None
    if arguments.proton:
        proton = ProtonInstallation(
            source="explicit-cli",
            name=arguments.proton.parent.name,
            version="explicit",
            root=arguments.proton.parent,
            executable=arguments.proton,
        )
    runtime_key = execution_fingerprint(
        preset_key,
        platform_name=platform_name,
        proton_selection=proton.selection_id if proton else None,
        display_backend=arguments.display_backend,
    )
    data_root = arguments.data_root.expanduser().resolve()
    staged = stage_runtime_job(
        preset,
        preset_directory=arguments.preset.parent,
        jobs_root=data_root / "jobs",
        runtime_key=runtime_key,
    )
    prefix = data_root / "prefixes" / f"frame-smoke-{runtime_key[:16]}-{arguments.display_backend}"
    if platform_name == "linux":
        prefix.mkdir(parents=True, exist_ok=True)
    steam_root = Path.home() / ".local" / "share" / "Steam"
    plan = build_launch_plan(
        platform_name=platform_name,
        sidecar=staged.files["sidecar"],
        working_directory=staged.directory,
        proton=proton,
        prefix=prefix if platform_name == "linux" else None,
        steam_client_path=steam_root if steam_root.is_dir() else None,
        dll_overrides=preset.wine_dll_overrides,
    )
    environment = os.environ.copy()
    environment.update(plan.environment)
    if platform_name == "linux":
        environment, _ = augment_graphical_session(environment)
        if arguments.display_backend == "xwayland":
            if not environment.get("DISPLAY"):
                raise RuntimeError("Xwayland smoke test requires DISPLAY")
            environment.update(
                PROTON_ENABLE_WAYLAND="0",
                PROTON_USE_WAYLAND="0",
                WINE_GRAPHICS_DRIVER="x11",
            )
        else:
            if not environment.get("WAYLAND_DISPLAY"):
                raise RuntimeError("native Wayland smoke test requires WAYLAND_DISPLAY")
            environment.update(
                PROTON_ENABLE_WAYLAND="1",
                PROTON_USE_WAYLAND="1",
                WINE_GRAPHICS_DRIVER="wayland",
            )
    environment["COMFY_DLSS_SMOKE_ID"] = staged.directory.name
    diagnostic_path = staged.directory / "frame-worker-result.json"
    authentication_token = secrets.token_bytes(32)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(120)
    tcp_port = listener.getsockname()[1]
    command = plan.command + (
        "--frame-worker",
        "--tcp",
        "127.0.0.1",
        str(tcp_port),
        authentication_token.hex(),
        "--presents",
        str(arguments.presents),
        "--interval-ms",
        "8",
        "--result",
        str(diagnostic_path),
    )
    log_path = staged.directory / "frame-worker-stderr.log"
    frame_reports = []
    with listener, log_path.open("wb") as log:
        process = subprocess.Popen(
            command, cwd=plan.working_directory, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
            start_new_session=os.name == "posix",
        )
        completed = False
        try:
            connection, peer = listener.accept()
            with connection:
                if peer[0] != "127.0.0.1":
                    raise RuntimeError("rejected non-loopback frame worker peer")
                connection.settimeout(120)
                with connection.makefile("rwb", buffering=0) as stream:
                    received = bytearray()
                    while len(received) < len(authentication_token):
                        chunk = stream.read(len(authentication_token) - len(received))
                        if not chunk:
                            raise EOFError("frame worker disconnected during authentication")
                        received.extend(chunk)
                    if not secrets.compare_digest(received, authentication_token):
                        raise RuntimeError("frame worker TCP authentication failed")
                    client = SidecarClient(stream, stream)
                    hello = client.hello()
                    if hello.get("ngx") is not False or not hello.get("guide_readback"):
                        raise ProtocolError("this test requires the diagnostic multi-plane worker")
                    configured = client.configure({"renderer": "d3d12-passthrough"})
                    if configured.get("accepted") is not False:
                        raise ProtocolError("diagnostic worker must not claim effect settings were applied")
                    for frame in smoke_frames():
                        # One request then one reply: no full-video buffering or pipe deadlock.
                        if frame.frame_index % 5 == 0:
                            if client.reset().get("reset") is not True:
                                raise ProtocolError("worker did not acknowledge RESET")
                        returned = client.process_frame(frame, reset_history=frame.frame_index % 5 == 0)
                        verify_readback(frame, returned)
                        frame_reports.append({
                            "index": frame.frame_index,
                            "size": [frame.input_width, frame.input_height],
                            "color_format": frame.planes[0].pixel_format.name,
                            "plane_count": len(frame.planes),
                            "identical": True,
                        })
                    shutdown = client.shutdown()
                    if shutdown.get("shutdown") is not True or stream.read(1):
                        raise ProtocolError("invalid SHUTDOWN acknowledgment or trailing bytes")
            process.wait(timeout=60)
            completed = True
        finally:
            if not completed:
                terminate_smoke_processes(process, staged.directory.name)
                print(f"Frame smoke failed; diagnostics: {staged.directory}", file=sys.stderr)

    with log_path.open("rb") as log:
        log.seek(max(0, log_path.stat().st_size - 32_000))
        stderr = log.read().decode("utf-8", errors="replace")
    diagnostic = {}
    if diagnostic_path.is_file():
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    report = {
        "schema_version": 1,
        "ok": process.returncode == 0 and bool(frame_reports) and bool(diagnostic.get("ok")),
        "job_directory": str(staged.directory),
        "hello": hello,
        "configured": configured,
        "frame_count": len(frame_reports),
        "plane_count": sum(frame["plane_count"] for frame in frame_reports),
        "frame_identical": all(frame["identical"] for frame in frame_reports),
        "frames": frame_reports,
        "shutdown": shutdown,
        "return_code": process.returncode,
        "diagnostic": diagnostic,
        "stderr": stderr,
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    (staged.directory / "frame-stream-report.json").write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
