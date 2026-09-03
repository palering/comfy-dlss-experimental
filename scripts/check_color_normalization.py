#!/usr/bin/env python3
"""Opt-in dedicated-server check: actual conversion, look-only cache reuse, export."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8199")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--test-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    identity = "dlss-color-check-" + uuid.uuid4().hex
    root = args.test_root.resolve(strict=True)
    video = root / "input" / (identity + ".mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i", "testsrc2=size=256x192:rate=24:duration=1",
                    "-vf", "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "2", str(video)],
                   check=True, capture_output=True, timeout=30)
    base = json.loads(args.prompt.read_text())
    base["client_id"] = identity
    base.setdefault("extra_data", {}).setdefault("extra_pnginfo", {}).setdefault("workflow", {})["id"] = identity
    base["prompt"]["1"]["inputs"]["file"] = video.name
    base["prompt"]["6"]["inputs"].update(start_time=0, duration=.5, preview_scale=50)
    base["prompt"]["9"] = {"class_type": "DLSSExperimentalNVIDIAFlow", "inputs": {
        "preset": "均衡 · Balanced", "output_grid": "4 × 4（较省资源）", "temporal_hints": True, "device": 0}}
    base["prompt"]["2"]["inputs"]["flow_provider"] = ["9", 0]
    results = {"fixture": str(video)}

    def api(path, data=None):
        request = urllib.request.Request(args.url + path, data=None if data is None else json.dumps(data).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def run(name, payload):
        queued = api("/prompt", payload)
        assert not queued.get("node_errors"), queued
        identifier = queued["prompt_id"]
        started = time.monotonic()
        while time.monotonic() - started < 180:
            history = api("/history/" + identifier).get(identifier)
            if history:
                assert history["status"]["status_str"] == "success", history["status"]
                results[name] = history
                print(name, "passed", round(time.monotonic() - started, 3), flush=True)
                return history
            time.sleep(.3)
        raise TimeoutError(identifier)

    def preview(name, payload, operation, cached):
        history = run(name, payload)
        data = history["outputs"]["6"]["dlss_preview"][0]
        assert data["report"]["passed"] and data["guide_mode"] == "nvidia"
        assert data["guide_cache_hit"] is cached
        assert data["input_report"]["color_normalization"]["operation"] == operation
        assert data["report"]["color_pipeline"]["operation"] == operation
        assert data["report"]["cleanup"]["remaining_owned_processes"] == 0
        return data

    queue = api("/queue")
    assert not queue["queue_running"] and not queue["queue_pending"], "Dedicated instance must be idle"
    schema = api("/object_info/DLSSExperimentalPrepareTemporalSequence")["DLSSExperimentalPrepareTemporalSequence"]
    toggle = schema["input"]["optional"]["normalize_to_srgb"]
    assert toggle[0] == "BOOLEAN" and toggle[1]["default"] is False
    try:
        off = preview("legacy_off", base, "disabled", False)
        on = copy.deepcopy(base)
        on["prompt"]["3"]["inputs"]["normalize_to_srgb"] = True
        normalized = preview("normalize_on", on, "bt709_to_srgb", False)
        assert normalized["report"]["raw_output_sha256"] != off["report"]["raw_output_sha256"]
        for intensity in (.4, .65):
            on["prompt"]["5"]["inputs"]["intensity"] = intensity
            preview("look_" + str(intensity), on, "bt709_to_srgb", True)
        # A Process Video request with identical range/resolution/pre-roll reuses
        # the normalized frames prepared for Preview. SaveVideo is the sink.
        full = copy.deepcopy(on)
        del full["prompt"]["6"]
        full["prompt"]["7"] = {"class_type": "DLSSExperimentalProcessVideo", "inputs": {
            "sequence": ["3", 0], "runtime": ["4", 0], "profile": ["5", 0], "start_time": 0, "duration": .5, "scale": 50}}
        full["prompt"]["8"] = {"class_type": "SaveVideo", "inputs": {
            "video": ["7", 0], "filename_prefix": identity + "/normalized", "format": "auto", "format.codec": "auto"}}
        temp = root / "temp" / "dlss-experimental"
        existing = set(temp.glob("render-*/result/report.json"))
        run("process_save_reuses_preview", full)
        reports = set(temp.glob("render-*/result/report.json")) - existing
        assert len(reports) == 1, reports
        process_report = json.loads(reports.pop().read_text())
        assert process_report["guide_cache_hit"]
        assert process_report["color_pipeline"]["working_transfer"] == "iec61966-2-1"
        assert process_report["color_pipeline"]["output_transfer"] == "bt709"
        results["process_report"] = process_report
        preview("return_to_off_cache", base, "disabled", True)
    finally:
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
