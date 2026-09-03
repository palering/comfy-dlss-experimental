#!/usr/bin/env python3
"""Opt-in idle dedicated-Comfy benchmark; no service/environment mutation."""
import argparse
import copy
import json
from pathlib import Path
import time
import urllib.request
import uuid


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8199")
    p.add_argument("--prompt", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--duration", type=float, default=2)
    p.add_argument("--scale", type=int, default=50)
    p.add_argument("--with-frame", action="store_true")
    p.add_argument("--runtime-preset")
    args = p.parse_args()

    def api(path, data=None):
        request = urllib.request.Request(args.url + path, data=json.dumps(data).encode() if data is not None else None,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    queue = api("/queue")
    assert not queue["queue_running"] and not queue["queue_pending"], "Test instance must be idle"
    base = json.loads(args.prompt.read_text())
    identity = "dlss-perf-" + uuid.uuid4().hex
    base["client_id"] = identity
    base.setdefault("extra_data", {}).setdefault("extra_pnginfo", {}).setdefault("workflow", {})["id"] = identity
    base["prompt"]["1"]["inputs"]["file"] = args.video
    if args.runtime_preset:
        base["prompt"]["4"]["inputs"]["runtime_preset"] = args.runtime_preset
    base["prompt"]["3"]["inputs"].update(color_policy="fill_missing", assumed_transfer="bt709", assumed_range="tv")
    base["prompt"]["6"]["inputs"].update(start_time=0, duration=args.duration, preview_scale=args.scale)
    base["prompt"]["9"] = {"class_type": "DLSSExperimentalNVIDIAFlow", "inputs": {
        "preset": "均衡 · Balanced", "output_grid": "4 × 4（较省资源）", "temporal_hints": True, "device": 0}}
    base["prompt"]["2"]["inputs"]["flow_provider"] = ["9", 0]
    results = []
    cases = [("range_1", "range", .4), ("range_2", "range", .4)]
    if args.with_frame:
        cases += [("frame_1", "frame", .4), ("frame_look", "frame", .6)]
    for name, mode, intensity in cases:
        payload = copy.deepcopy(base)
        payload["prompt"]["6"]["inputs"].update(preview_mode=mode, cursor_time=0.5 if mode == "frame" else 0)
        payload["prompt"]["5"]["inputs"]["intensity"] = intensity
        queued = api("/prompt", payload)
        identifier = queued["prompt_id"]
        started = time.monotonic()
        while time.monotonic() - started < 600:
            history = api("/history/" + identifier).get(identifier)
            if history:
                assert history["status"]["status_str"] == "success", history["status"]
                value = history["outputs"]["6"]["dlss_preview"][0]
                assert value["report"]["passed"]
                if mode == "frame":
                    assert value["frame_count"] == 1, value["frame_count"]
                results.append({"case": name, "wall_seconds": time.monotonic() - started, "session": value})
                execution = value.get("execution", {})
                print(json.dumps({"case": name, "total": execution.get("elapsed_seconds"),
                                  "stages": execution.get("stage_seconds"), "cache": value.get("guide_cache_hit"),
                                  "performance": value["report"].get("performance")}, ensure_ascii=False), flush=True)
                break
            time.sleep(.25)
        else:
            raise TimeoutError(identifier)
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
