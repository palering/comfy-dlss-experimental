#!/usr/bin/env python3
"""Explicit opt-in GPU diagnostic; --mock-suite never loads graphics/vendor DLLs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import time
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comfy_dlss_experimental.direct_nr import DirectNRClient, DirectNRSettings
from comfy_dlss_experimental.native_relay import NativeRelay


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fixture(width: int, height: int) -> bytes:
    # A stationary deterministic calibration chart; not evidence of visual quality.
    return bytes(component for y in range(height) for x in range(width)
                 for component in (x * 255 // max(width - 1, 1), y * 255 // max(height - 1, 1),
                                   220 if (x // 24 + y // 24) % 2 else 30, 255))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--relay", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--proton", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mock-suite", action="store_true")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--warmup", type=int, default=120)
    parser.add_argument("--intensity", type=float, default=1.0)
    parser.add_argument("--frames", type=int, default=2)
    args = parser.parse_args()
    root = args.root.resolve()
    run = root / ("mock" if args.mock_suite else "direct") / uuid.uuid4().hex[:12]
    run.mkdir(parents=True, exist_ok=False)
    env = {"PROTON_ENABLE_WAYLAND": "0", "PROTON_USE_WAYLAND": "0", "WINE_GRAPHICS_DRIVER": "x11"}
    if args.proton:
        # Caller explicitly supplies the active desktop session (no socket guessing).
        if not os.environ.get("DISPLAY"):
            parser.error("this smoke test requires the caller's DISPLAY")
        steam = Path.home() / ".local/share/Steam"
        if not steam.is_dir():
            parser.error("Steam client path is missing")
        env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(steam)
    for variable, directory in (("XDG_CACHE_HOME", "cache"), ("TMPDIR", "tmp")):
        path = root / directory
        path.mkdir(exist_ok=True)
        env[variable] = str(path)
    compatdata = root / ("prefix-mock" if args.mock_suite else "prefix-direct")
    modes = ["echo", "log_flood", "exit", "truncated", "bad_result", "hang", "disconnect", "timeout", "blocked_cancel"] if args.mock_suite else ["real"]
    reports = []
    report_path = run / "report.json"
    print(f"report: {report_path}", flush=True)
    for mode in modes:
        job = run / mode
        settings = DirectNRSettings(640, 360, 3 if args.mock_suite else args.frames,
                                    warmup=args.warmup, intensity=args.intensity)
        color = fixture(settings.width, settings.height)
        motion = struct.pack("<ee", 0.25, -0.5) * (settings.width * settings.height) if args.mock_suite else bytes(len(color))
        case_env = env | {"COMFY_DLSS_MOCK_MODE": "hang" if mode in ("disconnect", "timeout", "blocked_cancel") else mode}
        report = {"mode": mode, "settings": asdict(settings), "mock": args.mock_suite,
                  "relay_sha256": digest(args.relay), "worker_sha256": digest(args.worker), "frames": []}
        model = args.worker.parent / "nvngx_dlssnr.dll"
        if not args.mock_suite:
            if args.worker.name != "nvngx.dll" or not model.is_file():
                parser.error("real test requires original nvngx.dll worker beside nvngx_dlssnr.dll")
            report["model_sha256"] = digest(model)
        started = time.monotonic()
        session = None
        try:
            session = NativeRelay(args.relay, args.worker, job, proton=args.proton,
                                  compatdata=compatdata, environment=case_env, timeout=args.timeout,
                                  worker_timeout=3 if mode == "timeout" else args.timeout)
            report["processes"] = session.hello
            if mode == "hang":
                session.cancel()
                result = session.wait()
                if result["exit_code"] != 125 or result["reason"] != 1:
                    raise AssertionError(f"unexpected cancellation result {result}")
                report["exit"] = result
            elif mode == "disconnect":
                session.close()
            elif mode == "timeout":
                result = session.wait()
                if result != {"exit_code": 124, "reason": 2}:
                    raise AssertionError(f"unexpected timeout result {result}")
                report["exit"] = result
            elif mode == "blocked_cancel":
                writer_errors = []
                def fill_pipe():
                    try:
                        session.write(bytes(16 * 1024 * 1024))
                    except (OSError, RuntimeError) as exc:
                        writer_errors.append(str(exc))
                writer = threading.Thread(target=fill_pipe, daemon=True)
                writer.start()
                time.sleep(0.2)
                session.cancel()
                session.close()
                writer.join(2)
                if writer.is_alive():
                    raise AssertionError("blocked writer survived cancellation")
                report["writer_interrupted"] = bool(writer_errors)
            elif mode == "exit":
                result = session.wait()
                if result["exit_code"] != 23:
                    raise AssertionError(f"unexpected mock exit {result}")
                report["exit"] = result
            else:
                client = DirectNRClient(session, settings)
                for index in range(settings.frame_count):
                    output = client.process(color, motion, index * 41666667, reset=index == 2)
                    if args.mock_suite and output != color:
                        raise AssertionError("mock pipe roundtrip changed bytes")
                    changed = sum(a != b for a, b in zip(color, output))
                    report["frames"].append({"index": index, "bytes": len(output),
                                             "sha256": hashlib.sha256(output).hexdigest(), "changed_bytes": changed})
                    if not args.mock_suite:
                        (job / f"frame-{index:03d}.rgba").write_bytes(output)
                        if index == 0:
                            (job / "input.rgba").write_bytes(color)
                    print(f"{mode}: frame {index + 1}/{settings.frame_count}, changed_bytes={changed}", flush=True)
                report["exit"] = client.finish()
                if not args.mock_suite:
                    log = (job / "worker-stderr.log").read_text(encoding="utf-8", errors="replace")
                    create = re.search(r"direct feature 18 ready:.*result=0x([0-9a-fA-F]{8})", log)
                    completed = re.search(r"complete: (\d+) frames delivered, (\d+) direct evaluations", log)
                    if not create or int(create.group(1), 16) != 1 or not completed:
                        raise AssertionError("missing successful Feature 18 creation/completion evidence")
                    if int(completed.group(1)) != settings.frame_count or int(completed.group(2)) != settings.frame_count + settings.warmup:
                        raise AssertionError("worker evaluation counts do not match the requested stream")
                    report["feature_18_confirmed"] = True
                    report["successful_direct_evaluations"] = int(completed.group(2))
                    report["direct_create_result"] = "0x00000001"
                    report["visual_quality_verified"] = False
            if mode in ("truncated", "bad_result"):
                raise AssertionError("invalid mock output was accepted")
            report["passed"] = True
        except (RuntimeError, OSError, EOFError, TimeoutError, AssertionError) as exc:
            report["error"] = str(exc)
            report["passed"] = ((mode == "bad_result" and "0xBAD00002" in str(exc)) or
                                (mode == "truncated" and "worker exited" in str(exc)))
        finally:
            if session:
                try:
                    session.close()
                    report["cleanup"] = session.cleanup
                except Exception as exc:
                    report["cleanup_error"] = str(exc)
                    report["passed"] = False
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            reports.append(report)
            report_path.write_text(json.dumps(reports, indent=2), encoding="utf-8")
            print(json.dumps({key: value for key, value in report.items() if key in ("mode", "passed", "error", "cleanup_error", "elapsed_seconds")}), flush=True)
        if not report.get("passed"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
