#!/usr/bin/env python3
"""Bounded D5V2 reuse experiment; does not modify Comfy or external binaries."""
import argparse
from dataclasses import asdict
import hashlib
import json
import sys
import time
from pathlib import Path
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comfy_dlss_experimental.direct_nr import DirectNRClient, DirectNRSettings
from comfy_dlss_experimental.native_relay import NativeRelay
from comfy_dlss_experimental.video_pipeline import relay_environment


def main():
    import numpy as np
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--proton", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    with urllib.request.urlopen("http://127.0.0.1:8199/queue", timeout=5) as response:
        queue = json.load(response)
    assert not queue["queue_running"] and not queue["queue_pending"], "Test Comfy must be idle"
    candidates = []
    for path in args.prepared_root.glob("*/manifest.json"):
        manifest = json.loads(path.read_text())
        if (manifest["width"], manifest["height"]) == (280, 376) and len(manifest["frames"]) > 1:
            candidates.append((len(manifest["frames"]), path, manifest))
    assert candidates, "Need an existing 280x376 multi-frame prepared cache"
    _, path, manifest = max(candidates, key=lambda x: x[0])
    plane = manifest["width"] * manifest["height"] * 4
    count = min(12, len(manifest["frames"]))
    frames = []
    with (path.parent / "color.rgba").open("rb") as color, (path.parent / "motion.rg16f").open("rb") as motion:
        for frame in manifest["frames"][:count]:
            a, b = color.read(plane), motion.read(plane)
            assert hashlib.sha256(a).hexdigest() == frame["color_sha256"]
            assert hashlib.sha256(b).hexdigest() == frame["motion_sha256"]
            frames.append((a, b, frame["pts_ns"]))
    # A distractor with different colors, then the actual clip as B. Source files
    # remain untouched. We compare raw outputs against a fresh B feature.
    distractor = []
    for color, motion, pts in frames:
        rgba = np.frombuffer(color, dtype=np.uint8).reshape(-1, 4).copy()
        rgba[:, :3] = 255 - rgba[:, :3]
        distractor.append((rgba.tobytes(), motion, pts))
    run = args.root / ("stream-reuse-" + uuid.uuid4().hex[:12])
    run.mkdir(parents=True, exist_ok=False)
    preset = json.loads(args.preset.read_text())
    proton, env = relay_environment({"platform": "linux", "proton": {"executable": str(args.proton)},
                                     "linux_display_backend": "xwayland"}, run)
    outcomes = []

    def new_stream(name, count):
        relay = NativeRelay(Path(preset["components"]["relay"]), Path(preset["components"]["worker"]),
            run / name, proton=proton, compatdata=args.root / "reuse-probe-prefix", environment=env,
            timeout=60, worker_timeout=180)
        settings = DirectNRSettings(280, 376, count, warmup=120, intensity=1, style=2)
        return relay, DirectNRClient(relay, settings)

    def process_clip(client, clip, *, first_reset=True):
        return [client.process(c, m, pts, reset=first_reset and i == 0) for i, (c, m, pts) in enumerate(clip)]

    relay, client = new_stream("fresh", count)
    with relay:
        before = time.perf_counter()
        fresh = process_clip(client, frames)
        fresh_seconds = time.perf_counter() - before
        client.finish()
    strategies = [("reset_only", 0, False, True), ("warmup_first_reset", 120, False, False),
                  ("warmup_all_reset", 120, True, False), ("warmup_then_reset_first", 120, False, True)]
    total = sum(2 * count + warmup for _, warmup, _, _ in strategies)
    relay, client = new_stream("reused", total)
    with relay:
        for name, warmup, all_reset, target_reset in strategies:
            process_clip(client, distractor)
            # Verify that an idle gap does not end the worker stream.
            if name == "reset_only":
                time.sleep(1)
            before = time.perf_counter()
            color, motion, pts = frames[0]
            for i in range(warmup):
                client.process(color, motion, pts, reset=all_reset or i == 0)
            actual = process_clip(client, frames, first_reset=target_reset)
            seconds = time.perf_counter() - before
            diffs = [np.abs(np.frombuffer(a, np.uint8).astype(np.int16) - np.frombuffer(b, np.uint8).astype(np.int16))
                     for a, b in zip(fresh, actual)]
            result = {"strategy": name, "seconds": seconds, "exact": actual == fresh,
                      "mean_abs": float(np.mean([d.mean() for d in diffs])), "max_abs": int(max(d.max() for d in diffs)),
                      "first_mean_abs": float(diffs[0].mean()), "last_mean_abs": float(diffs[-1].mean())}
            outcomes.append(result)
            print(json.dumps(result), flush=True)
        client.finish()
    report = {"prepared": str(path.parent), "fresh_seconds": fresh_seconds,
              "settings": asdict(DirectNRSettings(280, 376, count, intensity=1, style=2)),
              "worker_hash": hashlib.sha256(Path(preset["components"]["worker"]).read_bytes()).hexdigest(),
              "outcomes": outcomes, "cleanup": relay.cleanup}
    (run / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"report": str(run / "report.json"), "fresh_seconds": fresh_seconds, "cleanup": relay.cleanup}), flush=True)


if __name__ == "__main__":
    main()
