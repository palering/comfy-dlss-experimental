#!/usr/bin/env python3
"""Opt-in Comfy test-server integration: provider connection, cache and NR output."""
import argparse
import copy
import json
from pathlib import Path
import time
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8199")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(args.prompt.read_text())
    base["client_id"] = "nvof-integration-" + uuid.uuid4().hex
    base["prompt"]["6"]["inputs"].update(start_time=0, duration=.5, preview_scale=50)
    results = {}

    def api(path, data=None):
        request = urllib.request.Request(args.url + path,
            data=None if data is None else json.dumps(data).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    queue = api("/queue")
    assert not queue["queue_running"] and not queue["queue_pending"], "Dedicated instance must be idle"
    definitions = api("/object_info")
    for name in ("DLSSExperimentalDISFlow", "DLSSExperimentalNVIDIAFlow"):
        assert definitions[name]["output"][0] == "DLSSE_OPTICAL_FLOW_PROVIDER"
    assert "flow_provider" in definitions["DLSSExperimentalTemporalSettings"]["input"]["optional"]

    def run(name, payload, expected_mode, cache=None):
        queued = api("/prompt", payload)
        assert not queued.get("node_errors"), queued
        identifier = queued["prompt_id"]
        started = time.monotonic()
        while time.monotonic() - started < 180:
            result = api("/history/" + identifier).get(identifier)
            if result:
                assert result["status"]["status_str"] == "success", result["status"]
                preview = result["outputs"]["6"]["dlss_preview"][0]
                assert preview["guide_mode"] == expected_mode
                assert result["outputs"]["3"]["dlss_input_report"][0]["guide_mode"] == expected_mode
                assert preview["report"]["passed"]
                assert preview["report"]["cleanup"]["remaining_owned_processes"] == 0
                if cache is not None:
                    assert preview["guide_cache_hit"] is cache
                results[name] = {"prompt_id": identifier, "seconds": round(time.monotonic() - started, 3), **preview}
                print(name, "passed", "frames", preview["frame_count"], "cache", preview["guide_cache_hit"], flush=True)
                return preview
            time.sleep(.3)
        raise TimeoutError(identifier)

    try:
        run("legacy_dis", copy.deepcopy(base), "dis")
        nvidia = copy.deepcopy(base)
        nvidia["prompt"]["9"] = {"class_type": "DLSSExperimentalNVIDIAFlow", "inputs": {
            "preset": "均衡 · Balanced", "output_grid": "4 × 4（较省资源）", "device": 0, "temporal_hints": True}}
        nvidia["prompt"]["2"]["inputs"].update(flow_provider=["9", 0], motion_provider="zero")
        run("nvidia_grid4", nvidia, "nvidia")
        run("nvidia_cache", nvidia, "nvidia", True)
        grid1 = copy.deepcopy(nvidia)
        grid1["prompt"]["9"]["inputs"]["output_grid"] = "1 × 1（逐像素输出）"
        result = run("nvidia_grid1", grid1, "nvidia")
        assert result["guide_settings"]["flow"]["output_grid"] == 1
        connected_dis = copy.deepcopy(nvidia)
        connected_dis["prompt"]["9"] = {"class_type": "DLSSExperimentalDISFlow", "inputs": {"preset": "均衡 · Balanced"}}
        run("connected_dis", connected_dis, "dis")
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
