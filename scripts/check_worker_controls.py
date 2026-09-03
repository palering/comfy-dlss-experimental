#!/usr/bin/env python3
"""Opt-in release/telemetry regression on an idle, dedicated Comfy instance."""
import argparse
import copy
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8199")
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--video", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--resident", action="store_true")
    parser.add_argument("--frame-only", action="store_true", help="Only render the frame; resident mode leaves an idle instance for UI release checks")
    parser.add_argument("--idle-timeout", type=int, default=30)
    args = parser.parse_args()

    def api(path, data=None, expected=200):
        request = urllib.request.Request(args.url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            result = json.load(response)
            assert response.status == expected, (response.status, result)
            return result

    queue = api("/queue")
    assert not queue["queue_running"] and not queue["queue_pending"], "Dedicated test instance must be idle"
    assert api("/dlss-experimental/executions")["lifecycle"]["lazy_start"] is True
    template = json.loads(args.prompt.read_text())
    template["prompt"]["4"]["inputs"].update(keep_worker_alive=args.resident, idle_timeout_seconds=args.idle_timeout)
    template["prompt"]["1"]["inputs"]["file"] = args.video
    template["prompt"]["3"]["inputs"].update(color_policy="fill_missing", assumed_transfer="bt709", assumed_range="tv")
    template["prompt"]["9"] = {"class_type": "DLSSExperimentalNVIDIAFlow", "inputs": {
        "preset": "均衡 · Balanced", "output_grid": "4 × 4（较省资源）", "temporal_hints": True, "device": 0}}
    template["prompt"]["2"]["inputs"]["flow_provider"] = ["9", 0]
    reports = []
    for mode in (("frame",) if args.frame_only else ("frame", "release")):
        payload = copy.deepcopy(template)
        identity = "worker-controls-" + uuid.uuid4().hex
        payload["client_id"] = identity
        payload["extra_data"]["extra_pnginfo"]["workflow"]["id"] = identity
        payload["prompt"]["6"]["inputs"].update(preview_mode="frame" if mode == "frame" else "range",
            cursor_time=.5833333333333334 if mode == "frame" else 0, start_time=0, duration=2 if mode == "frame" else 10,
            preview_scale=50)
        prompt_id = api("/prompt", payload)["prompt_id"]
        requested = False
        saw_live = False
        last_record = None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = api("/dlss-experimental/executions")
            active = [x for x in status["active"] if x.get("workflow_id") == identity]
            if active:
                record = active[0]
                saw_live |= record["worker_state"] == "running"
                if mode == "release" and record["worker_state"] == "running" and not requested:
                    response = api(f'/dlss-experimental/executions/{record["execution_id"]}/release-worker',
                                   {"confirm_terminate": True}, expected=202)
                    assert response["accepted"]
                    requested = True
            history = api("/history/" + prompt_id).get(prompt_id)
            completed = [x for x in status["history"] if x.get("workflow_id") == identity]
            if history and completed:
                last_record = completed[0]
                assert last_record["worker_state"] == ("idle" if args.resident and mode == "frame" else "released"), last_record
                assert last_record["state"] == ("success" if mode == "frame" else "cancelled"), last_record
                if mode == "frame":
                    assert history["status"]["status_str"] == "success", history["status"]
                    assert history["outputs"]["6"]["dlss_preview"][0]["frame_count"] == 1
                else:
                    assert requested and last_record["release_requested"]
                stale = api(f'/dlss-experimental/executions/{last_record["execution_id"]}/release-worker',
                            {"confirm_terminate": True}, expected=409)
                assert not stale["accepted"]
                reports.append({"case": mode, "saw_live_worker": saw_live, "execution": last_record})
                print(json.dumps({"case": mode, "state": last_record["state"], "worker": last_record["worker_state"],
                    "seconds": last_record["elapsed_seconds"], "saw_live": saw_live,
                    "rss_peak_mib": last_record["sampled_peak_rss_mib"],
                    "worker_vram_peak_mib": last_record["sampled_peak_worker_vram_mib"]}), flush=True)
                break
            time.sleep(.1)
        if last_record is None:
            raise TimeoutError(f"Regression did not finish: {identity}; no global interruption is sent")
        args.report.write_text(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
