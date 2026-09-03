#!/usr/bin/env python3
"""Opt-in end-to-end residency test on an idle dedicated Comfy instance."""
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
    p.add_argument("--video", required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    def api(path, value=None):
        request = urllib.request.Request(args.url + path, data=json.dumps(value).encode() if value is not None else None,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    def residents():
        return api("/dlss-experimental/executions")["resident"]["workers"]
    queue = api("/queue")
    assert not queue["queue_running"] and not queue["queue_pending"] and not residents(), "Test instance must be idle with no resident worker"
    base = json.loads(args.prompt.read_text())
    base["prompt"]["1"]["inputs"]["file"] = args.video
    base["prompt"]["3"]["inputs"].update(color_policy="fill_missing", assumed_transfer="bt709", assumed_range="tv")
    base["prompt"]["9"] = {"class_type": "DLSSExperimentalNVIDIAFlow", "inputs": {
        "preset": "均衡 · Balanced", "output_grid": "4 × 4（较省资源）", "temporal_hints": True, "device": 0}}
    base["prompt"]["2"]["inputs"]["flow_provider"] = ["9", 0]
    results = {}
    owned = set()
    def run(name, persistent=True, cursor=.5833333333333334, intensity=1.0, scale=50):
        queue = api("/queue")
        assert not queue["queue_running"] and not queue["queue_pending"], "Another task arrived; stop testing"
        payload = copy.deepcopy(base)
        identity = "resident-check-" + uuid.uuid4().hex
        payload["client_id"] = identity
        payload["extra_data"]["extra_pnginfo"]["workflow"]["id"] = identity
        payload["prompt"]["4"]["inputs"].update(keep_worker_alive=persistent, idle_timeout_seconds=30)
        payload["prompt"]["5"]["inputs"].update(intensity=intensity)
        payload["prompt"]["6"]["inputs"].update(preview_mode="frame", cursor_time=cursor, preview_scale=scale, duration=2)
        identifier = api("/prompt", payload)["prompt_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            history = api("/history/" + identifier).get(identifier)
            if history:
                assert history["status"]["status_str"] == "success", history["status"]
                session = history["outputs"]["6"]["dlss_preview"][0]
                record = session["execution"]
                assert session["frame_count"] == 1 and session["report"]["passed"]
                result = {"execution": record, "report": session["report"], "workers": residents()}
                results[name] = result
                if persistent:
                    assert len(result["workers"]) == 1 and result["workers"][0]["state"] == "idle"
                    owned.add(result["workers"][0]["worker_id"])
                else:
                    assert result["workers"] == []
                print(json.dumps({"case": name, "seconds": record["elapsed_seconds"], "worker_reused": record.get("worker_reused"),
                    "worker_id": record.get("worker_id"), "worker_state": record["worker_state"],
                    "raw_sha256": session["report"]["raw_output_sha256"]}), flush=True)
                args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))
                return result
            time.sleep(.15)
        raise TimeoutError(identity)
    try:
        a = run("cold")
        b = run("warm")
        assert not a["execution"]["worker_reused"] and b["execution"]["worker_reused"]
        assert a["report"]["raw_output_sha256"] == b["report"]["raw_output_sha256"]
        seek = run("seek_warm", cursor=.8333333333333334)
        assert seek["execution"]["worker_reused"]
        isolated = run("seek_isolated", persistent=False, cursor=.8333333333333334)
        assert seek["report"]["raw_output_sha256"] == isolated["report"]["raw_output_sha256"]
        look = run("look_cold", intensity=.6)
        assert not look["execution"]["worker_reused"]
        look2 = run("look_warm", intensity=.6)
        assert look2["execution"]["worker_reused"]
        assert look["report"]["raw_output_sha256"] == look2["report"]["raw_output_sha256"]
        changed = run("look_changed", intensity=1)
        assert not changed["execution"]["worker_reused"]
        native = run("size_changed", scale=100)
        assert not native["execution"]["worker_reused"]
        native2 = run("size_warm", scale=100)
        assert native2["execution"]["worker_reused"]
        assert native["report"]["raw_output_sha256"] == native2["report"]["raw_output_sha256"]
        worker_id = native2["execution"]["worker_id"]
        # Give the idle sampler one interval, then verify real idle PID resources.
        time.sleep(1.2)
        sample = residents()[0]
        assert sample["worker_id"] == worker_id and sample["resources"]["worker"]["processes"]
        results["idle_resources"] = sample
        assert api(f"/dlss-experimental/workers/{worker_id}/release", {"mode": "idle"})["accepted"]
        deadline = time.monotonic() + 10
        while residents() and time.monotonic() < deadline:
            time.sleep(.2)
        assert residents() == [], "Manual idle release did not complete"
        run("timeout_cold")
        print("Waiting for the configured 30-second idle expiry (no rendering during this wait).", flush=True)
        deadline = time.monotonic() + 40
        while residents() and time.monotonic() < deadline:
            time.sleep(.5)
        assert residents() == [], "Idle timeout did not release worker"
        results["lifecycle_passed"] = True
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print("PASS: exact reset outputs, reuse, look/size rebuild, isolated switch, idle telemetry, manual release, TTL.", flush=True)
    finally:
        # Only idle workers created by this test; never interrupt another task.
        for worker in residents():
            if worker["worker_id"] in owned and worker["state"] == "idle":
                api(f'/dlss-experimental/workers/{worker["worker_id"]}/release', {"mode": "idle"})


if __name__ == "__main__":
    main()
