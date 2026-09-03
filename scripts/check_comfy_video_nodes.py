#!/usr/bin/env python3
"""Opt-in integration check against a dedicated Comfy test server.

Requires a prepared preview API JSON, no vendor binaries are downloaded.
Writes only a report under --report and test outputs via Comfy's own folders.
"""
import argparse
import copy
import json
from pathlib import Path
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8199")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(args.prompt.read_text())
    results = {}

    def api(path, data=None):
        request = urllib.request.Request(args.url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)

    def run(name, payload, cancel=False):
        before = {r["session_id"] for r in api("/dlss-experimental/preview/sessions")["sessions"]}
        identifier = api("/prompt", payload)["prompt_id"]
        started = time.monotonic()
        cancelled = False
        while time.monotonic() - started < 180:
            history = api("/history/" + identifier).get(identifier)
            if history:
                results[name] = history
                print(name, history["status"]["status_str"], round(time.monotonic() - started, 2), flush=True)
                return history
            if cancel and not cancelled:
                for record in api("/dlss-experimental/preview/sessions")["sessions"]:
                    if record["session_id"] not in before and record.get("progress", {}).get("stage") == "rendering":
                        api("/dlss-experimental/preview/sessions/" + record["session_id"] + "/cancel", {})
                        cancelled = True
            time.sleep(0.2)
        raise TimeoutError(name)

    try:
        cached = run("cached_preview", base)
        preview = cached["outputs"]["6"]["dlss_preview"][0]
        assert preview["state"] == "ready" and preview["guide_cache_hit"]
        assert preview["report"]["cleanup"]["remaining_owned_processes"] == 0
        assert preview["frame_count"] == 48

        bypass = copy.deepcopy(base)
        bypass["prompt"]["5"]["inputs"]["mix"] = 0
        result = run("bypass", bypass)["outputs"]["6"]["dlss_preview"][0]
        assert result["report"]["bypassed"] and "cleanup" not in result["report"]

        cancelling = copy.deepcopy(base)
        cancelling["prompt"]["6"]["inputs"].update(duration=8, preview_scale=100)
        result = run("cancellation", cancelling, cancel=True)
        assert result["status"]["status_str"] == "error"
        assert any(r["state"] == "cancelled" for r in api("/dlss-experimental/preview/sessions")["sessions"])

        output = copy.deepcopy(base)
        del output["prompt"]["6"]
        output["prompt"]["7"] = {"class_type": "DLSSExperimentalProcessVideo", "inputs": {
            "sequence": ["3", 0], "runtime": ["4", 0], "profile": ["5", 0],
            "start_time": 0, "duration": 0, "scale": 100}}
        output["prompt"]["8"] = {"class_type": "SaveVideo", "inputs": {
            "video": ["7", 0], "filename_prefix": "dlss-test/full-video",
            "format": "auto", "format.codec": "auto"}}
        result = run("full_video", output)
        assert result["status"]["status_str"] == "success", result["status"]
        print(json.dumps(result["outputs"].get("8"), indent=2), flush=True)
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
