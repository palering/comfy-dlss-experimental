#!/usr/bin/env python3
"""Opt-in one-frame acceptance for Setup Helper, Sequence Hub and NR Pass Stack."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import time
import urllib.request
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8199")
    parser.add_argument("--prompt", type=Path, required=True,
                        help="Existing six-node Preview API payload; it is read but never modified")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--video", help="Optional Comfy input filename")
    parser.add_argument("--cursor", type=float, default=0.25)
    parser.add_argument("--scale", type=int, choices=(50, 75, 100), default=50)
    parser.add_argument("--discard-prepared-cache", action="store_true",
                        help="Disable prepared-cache retention and assert exact post-success cleanup")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    def api(path: str, data=None):
        request = urllib.request.Request(
            args.url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    queue = api("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Test instance must be idle")

    source = json.loads(args.prompt.read_text(encoding="utf-8"))
    payload = {"prompt": copy.deepcopy(source["prompt"])}

    def node_id(class_type: str) -> str:
        matches = [identifier for identifier, node in payload["prompt"].items()
                   if node.get("class_type") == class_type]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one {class_type} node")
        return matches[0]

    load = node_id("LoadVideo")
    adapter = node_id("DLSSExperimentalPrepareTemporalSequence")
    runtime = node_id("DLSSExperimentalRuntimeConfig")
    look = node_id("DLSSExperimentalNRProfile")
    preview = node_id("DLSSExperimentalPreviewSession")
    if args.video:
        payload["prompt"][load]["inputs"]["file"] = args.video
    payload["prompt"][adapter]["inputs"].update(
        color_policy="fill_missing", assumed_transfer="bt709", assumed_range="tv"
    )

    next_id = max(int(identifier) for identifier in payload["prompt"]) + 1
    hub, helper, stack = (str(next_id), str(next_id + 1), str(next_id + 2))
    payload["prompt"][hub] = {
        "class_type": "DLSSExperimentalSequenceHub",
        "inputs": {"sequence": [adapter, 0]},
    }
    payload["prompt"][helper] = {
        "class_type": "DLSSExperimentalSetupHelper",
        "inputs": {"runtime": [runtime, 0], "sequence": [hub, 1],
                   "verify_nvidia_flow": False, "include_diagnostics": False},
    }
    payload["prompt"][stack] = {
        "class_type": "DLSSExperimentalNRPassStack",
        "inputs": {"pass_count": 2, "pass_1": [look, 0]},
    }
    payload["prompt"][preview]["inputs"].update(
        sequence=[hub, 0], runtime=[helper, 0], profile_b=[stack, 0],
        start_time=0, duration=2, preview_scale=args.scale,
        preview_mode="frame", cursor_time=args.cursor,
        retain_prepared_cache=not args.discard_prepared_cache,
    )
    identity = "dlss-stack-hub-" + uuid.uuid4().hex
    payload["client_id"] = identity
    payload["extra_data"] = {"extra_pnginfo": {"workflow": {"id": identity, "revision": 0}}}

    prompt_id = api("/prompt", payload)["prompt_id"]
    started = time.monotonic()
    while time.monotonic() - started < args.timeout:
        record = api("/history/" + prompt_id).get(prompt_id)
        if record:
            args.report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            if record["status"]["status_str"] != "success":
                raise AssertionError(record["status"])
            setup = record["outputs"][helper]["dlss_setup_report"][0]
            result = record["outputs"][preview]["dlss_preview"][0]
            report = result["report"]
            assert setup["ready"], setup
            assert result["state"] == "ready" and result["frame_count"] == 1, result
            assert result["labels"]["b"] == "2-pass Stack B", result["labels"]
            assert report["passed"] and report["pass_count"] == 2, report
            assert len(report["passes"]) == 2 and all(item["passed"] for item in report["passes"]), report
            assert report["intermediate_video_encoding"] is False, report
            retention = report["prepared_cache_retention"]
            if args.discard_prepared_cache:
                assert retention["policy"] == "discard_after_success" and retention["removed"], retention
            final_queue = api("/queue")
            assert not final_queue["queue_running"] and not final_queue["queue_pending"], final_queue
            print(json.dumps({
                "prompt_id": prompt_id,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "setup_ready": setup["ready"],
                "frame_count": result["frame_count"],
                "guide_cache_hit": result.get("guide_cache_hit"),
                "label": result["labels"]["b"],
                "pass_count": report["pass_count"],
                "intermediate_video_encoding": report["intermediate_video_encoding"],
                "prepared_cache_retention": retention,
            }, ensure_ascii=False, indent=2))
            return
        time.sleep(0.25)
    raise TimeoutError(prompt_id)


if __name__ == "__main__":
    main()
