#!/usr/bin/env python3
"""Opt-in real-video diagnostic. Never starts/restarts or modifies ComfyUI."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comfy_dlss_experimental.direct_nr import DirectNRClient, DirectNRSettings
from comfy_dlss_experimental.media_clip import ClipRequest, export_video, file_hash, prepare_clip
from comfy_dlss_experimental.native_relay import NativeRelay


def process_variant(args, prepared: Path, manifest: dict, run: Path, mode: str, intensity: float, name: str) -> dict:
    import numpy as np
    from PIL import Image
    directory = run / name
    directory.mkdir()
    width, height = manifest["width"], manifest["height"]
    plane = width * height * 4
    settings = DirectNRSettings(width, height, len(manifest["frames"]), warmup=args.warmup,
                                intensity=intensity)
    result = {"mode": mode, "intensity": intensity, "frames": [], "passed": False}
    session = None
    started = time.monotonic()
    env = {"DISPLAY": os.environ.get("DISPLAY", ""), "PROTON_ENABLE_WAYLAND": "0", "PROTON_USE_WAYLAND": "0",
           "WINE_GRAPHICS_DRIVER": "x11", "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(Path.home() / ".local/share/Steam"),
           "XDG_CACHE_HOME": str(args.root / "cache"), "TMPDIR": str(args.root / "tmp")}
    for cache_directory_name in ("cache", "tmp"):
        (args.root / cache_directory_name).mkdir(exist_ok=True)
    try:
        session = NativeRelay(args.relay, args.worker, directory / "worker", proton=args.proton,
                              compatdata=args.root / "prefix-direct", environment=env, timeout=600)
        client = DirectNRClient(session, settings)
        with (prepared / "color.rgba").open("rb") as colors, (prepared / "motion.rg16f").open("rb") as motions, (directory / "processed.rgba").open("xb") as output:
            visible_index = 0
            for index, frame in enumerate(manifest["frames"]):
                color = colors.read(plane)
                flow = motions.read(plane)
                if len(color) != plane or len(flow) != plane:
                    raise ValueError("truncated prepared frame spool")
                if hashlib.sha256(color).hexdigest() != frame["color_sha256"]:
                    raise ValueError("prepared color frame changed")
                processed = client.process(color, bytes(plane) if mode == "zero" else flow,
                                           frame["pts_ns"], reset=frame["reset"])
                if frame["visible"]:
                    output.write(processed)
                    original = np.frombuffer(color, np.uint8).reshape(height, width, 4)
                    rendered = np.frombuffer(processed, np.uint8).reshape(height, width, 4)
                    difference = np.abs(rendered[..., :3].astype(np.int16) - original[..., :3].astype(np.int16))
                    result["frames"].append({"pts_ns": frame["pts_ns"], "mae_rgb": float(difference.mean()),
                                             "sha256": hashlib.sha256(processed).hexdigest(),
                                             "source_mean_rgb": original[..., :3].mean(axis=(0, 1)).tolist(),
                                             "output_mean_rgb": rendered[..., :3].mean(axis=(0, 1)).tolist(),
                                             "alpha_min": int(rendered[..., 3].min()), "alpha_max": int(rendered[..., 3].max())})
                    if visible_index in (0, manifest["visible_count"] // 2, manifest["visible_count"] - 1):
                        Image.fromarray(original[..., :3]).save(directory / f"source-{visible_index:03d}.png")
                        Image.fromarray(rendered[..., :3]).save(directory / f"processed-{visible_index:03d}.png")
                    visible_index += 1
                if (index + 1) % 12 == 0:
                    print(f"{name}: processed {index + 1}/{settings.frame_count} incl. pre-roll", flush=True)
        result["exit"] = client.finish()
        log = (directory / "worker/worker-stderr.log").read_text(errors="replace")
        evidence = re.search(r"complete: (\d+) frames delivered, (\d+) direct evaluations", log)
        if "direct feature 18 ready:" not in log or not evidence or tuple(map(int, evidence.groups())) != (settings.frame_count, settings.frame_count + settings.warmup):
            raise RuntimeError("missing Feature 18 completion evidence")
        result["evaluations"] = settings.frame_count + settings.warmup
        result["feature_18_confirmed"] = True
    finally:
        if session:
            session.close()
            result["cleanup"] = session.cleanup
        result["processing_seconds"] = round(time.monotonic() - started, 3)
        (directory / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    export_video(directory / "processed.rgba", directory / "processed.mp4", manifest)
    result["passed"] = True
    result["output"] = str(directory / "processed.mp4")
    (directory / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--relay", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--proton", type=Path)
    parser.add_argument("--start", type=float, default=1)
    parser.add_argument("--duration", type=float, default=2)
    parser.add_argument("--pre-roll", type=float, default=0.5)
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--intensity", type=float, default=1)
    parser.add_argument("--intensities", nargs="+", type=float, help="Optional A/B intensities sharing one prepared clip")
    parser.add_argument("--warmup", type=int, default=120)
    parser.add_argument("--modes", nargs="+", choices=("zero", "dis"), default=["zero", "dis"])
    args = parser.parse_args()
    args.root = args.root.resolve()
    if args.proton and not os.environ.get("DISPLAY"):
        parser.error("Xwayland diagnostic requires the caller's DISPLAY")
    if len(set(args.modes)) != len(args.modes):
        parser.error("duplicate modes")
    intensities = args.intensities if args.intensities is not None else [args.intensity]
    if len(intensities) > 4 or len(set(intensities)) != len(intensities):
        parser.error("choose at most four distinct intensity values")
    for intensity in intensities:
        DirectNRSettings(64, 64, 1, warmup=args.warmup, intensity=intensity).validate()
    run = args.root / "video" / uuid.uuid4().hex[:12]
    run.mkdir(parents=True)
    print(f"run: {run}", flush=True)
    report = {"source_sha256": file_hash(args.source), "relay_sha256": file_hash(args.relay),
              "worker_sha256": file_hash(args.worker), "model_sha256": file_hash(args.worker.parent / "nvngx_dlssnr.dll"),
              "passed": False, "visual_quality_approved": False, "variants": []}
    started = time.monotonic()
    try:
        manifest = prepare_clip(args.source, run / "prepared", ClipRequest(args.start, args.duration, args.pre_roll, args.scale))
        report["clip"] = {key: manifest[key] for key in ("width", "height", "fps", "visible_count", "visible_start_index", "request", "guide_settings", "color_contract")}
        report["preparation_seconds"] = round(time.monotonic() - started, 3)
        print(f"prepared {len(manifest['frames'])} frames; visible={manifest['visible_count']}", flush=True)
        plane = manifest["width"] * manifest["height"] * 4
        with (run / "prepared/color.rgba").open("rb") as source, (run / "original.rgba").open("xb") as target:
            source.seek(manifest["visible_start_index"] * plane)
            for _ in range(manifest["visible_count"]):
                frame = source.read(plane)
                if len(frame) != plane:
                    raise ValueError("truncated source spool")
                target.write(frame)
        export_video(run / "original.rgba", run / "original.mp4", manifest)
        for intensity in intensities:
            for mode in args.modes:
                name = mode if len(intensities) == 1 else f"{mode}-i{intensity:g}"
                report["variants"].append(process_variant(args, run / "prepared", manifest, run, mode, intensity, name))
        report["passed"] = all(v["passed"] for v in report["variants"])
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (run / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"report: {run / 'report.json'}, passed={report['passed']}", flush=True)


if __name__ == "__main__":
    main()
