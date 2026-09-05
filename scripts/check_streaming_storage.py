#!/usr/bin/env python3
"""Opt-in GPU acceptance: cached/streamed equality, stack histories and managed cleanup."""
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
    parser.add_argument("--nvidia", action="store_true")
    args = parser.parse_args()

    def api(path, body=None):
        request = urllib.request.Request(args.url + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    queue = api("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise ValueError("Dedicated test server must be idle")
    initial = api("/dlss-experimental/storage")
    original_settings = initial["settings"]
    source = json.loads(args.prompt.read_text())
    base = {"prompt": source["prompt"]}
    base["prompt"]["3"]["inputs"].update(color_policy="fill_missing", assumed_transfer="bt709", assumed_range="tv", normalize_to_srgb=True)
    base["prompt"]["4"]["inputs"].update(keep_worker_alive=False, worker_policy="isolated")
    if args.nvidia:
        base["prompt"]["9"] = {"class_type": "DLSSExperimentalNVIDIAFlow", "inputs": {
            "preset": "均衡 · Balanced", "output_grid": "4 × 4（较省资源）", "temporal_hints": True, "device": 0}}
        base["prompt"]["2"]["inputs"]["flow_provider"] = ["9", 0]
    results = {}
    def run(name, threshold, passes=1, duration=.5, process=False, persistent=False):
        api("/dlss-experimental/storage/settings", {"confirm": True, "settings": original_settings | {"entry_mib": threshold}})
        payload = copy.deepcopy(base)
        identity = "storage-acceptance-" + uuid.uuid4().hex
        payload.update(client_id=identity, extra_data={"extra_pnginfo": {"workflow": {"id": identity}}})
        payload["prompt"]["4"]["inputs"]["keep_worker_alive"] = persistent
        profile = ["5", 0]
        if passes > 1:
            payload["prompt"]["10"] = {"class_type": "DLSSExperimentalNRPassStack", "inputs": {"pass_count": passes, "pass_1": profile}}
            profile = ["10", 0]
        payload["prompt"]["6"]["inputs"].update(start_time=.5, duration=duration, process_to_end=False,
            preview_scale=50, preview_mode="range", cursor_time=0, profile_b=profile, retain_prepared_cache=True)
        if process:
            del payload["prompt"]["6"]
            payload["prompt"]["7"] = {"class_type": "DLSSExperimentalProcessVideo", "inputs": {
                "sequence": ["3", 0], "runtime": ["4", 0], "profile": profile,
                "start_time": .5, "duration": duration, "scale": 50}}
            payload["prompt"]["8"] = {"class_type": "SaveVideo", "inputs": {
                "video": ["7", 0], "filename_prefix": "dlss-storage-acceptance/" + identity,
                "format": "auto", "format.codec": "auto"}}
        start = time.monotonic()
        prompt_id = api("/prompt", payload)["prompt_id"]
        deadline = start + 300
        while time.monotonic() < deadline:
            record = api("/history/" + prompt_id).get(prompt_id)
            if record:
                results[name] = record
                args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))
                if record["status"]["status_str"] != "success":
                    raise RuntimeError(f"{name} failed: {record['status']}")
                if process:
                    status = api("/dlss-experimental/executions")
                    execution = next(r for r in status["history"] if r["kind"] == "process")
                    assert execution["state"] == "success"
                    print(json.dumps({"case": name, "seconds": round(time.monotonic() - start, 3), "storage": execution.get("storage_usage")}), flush=True)
                    return execution
                preview = record["outputs"]["6"]["dlss_preview"][0]
                result = preview["report"]
                if threshold == 1:
                    assert result["storage_mode"] == "streaming" and result["raw_disk_bytes"] == 0, result
                    assert preview["execution"]["storage_plan"]["queued_frames"] == 1
                print(json.dumps({"case": name, "seconds": round(time.monotonic() - start, 3),
                    "frames": preview["frame_count"], "raw_hash": result["raw_output_sha256"],
                    "storage": preview["execution"].get("storage_usage"),
                    "rss_mib": preview["execution"].get("sampled_peak_rss_mib"),
                    "vram_mib": preview["execution"].get("sampled_peak_worker_vram_mib")}), flush=True)
                return result
            time.sleep(.25)
        raise TimeoutError(prompt_id)
    try:
        for passes in (1, 2, 3):
            cached = run(f"cached_{passes}", 128, passes)
            streamed = run(f"streamed_{passes}", 1, passes)
            assert cached["raw_output_sha256"] == streamed["raw_output_sha256"], f"Raw pixels differ for {passes} passes"
        run("streamed_range", 1, duration=5)
        run("streamed_process_save", 1, duration=.5, process=True)
        first = run("resident_first", 1, persistent=True)
        second = run("resident_reused", 1, persistent=True)
        assert second["resident"]["reused"]
        assert first["raw_output_sha256"] == second["raw_output_sha256"]
        status = api("/dlss-experimental/executions")
        for worker in status["resident"]["workers"]:
            api("/dlss-experimental/workers/" + worker["worker_id"] + "/release", {"mode": "idle"})
        results["passed"] = True
    finally:
        # Restore limits even on a failed case; leave all evidence for inspection.
        api("/dlss-experimental/storage/settings", {"confirm": True, "settings": original_settings})
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
