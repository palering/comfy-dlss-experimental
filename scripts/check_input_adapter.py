#!/usr/bin/env python3
"""Opt-in real-GPU regression against a dedicated Comfy test server.

Uses the existing test workflow's node IDs 1..6. Does not mutate source files
or the user's loaded workflow. Interpretation of untagged input is an explicit
test assumption, not a claim about the source's true color encoding.
"""
import argparse
import copy
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8199")
    p.add_argument("--prompt", type=Path, required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--tagged-video", required=True, help="Tagged SDR regression input already available in Comfy")
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    base = json.loads(args.prompt.read_text())
    base["client_id"] = "dlss-input-adapter-test"
    base["prompt"]["1"]["inputs"]["file"] = args.video
    results = {}

    def api(path, data=None):
        request = urllib.request.Request(args.url + path, data=None if data is None else json.dumps(data).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode()) from error

    def run(name, payload):
        identifier = api("/prompt", payload)["prompt_id"]
        started = time.monotonic()
        while time.monotonic() - started < 180:
            result = api("/history/" + identifier).get(identifier)
            if result:
                results[name] = result
                print(name, result["status"]["status_str"], round(time.monotonic() - started, 2), flush=True)
                return result
            time.sleep(.3)
        raise TimeoutError(name)

    def preview(result):
        assert result["status"]["status_str"] == "success", result["status"]
        report = result["outputs"]["6"]["dlss_preview"][0]
        assert report["report"]["passed"]
        assert report["report"]["cleanup"]["remaining_owned_processes"] == 0
        assert report["frame_count"] == 48
        return report

    try:
        check = copy.deepcopy(base)
        check["prompt"] = {k:v for k,v in check["prompt"].items() if k in ("1", "2", "3")}
        inspected = run("strict_inspection", check)
        report = inspected["outputs"]["3"]["dlss_input_report"][0]
        assert report["state"] == "needs_confirmation" and len(report["issues"]) == 4
        assert "6" not in inspected["outputs"]
        rejected = run("strict_preview_rejected", base)
        assert rejected["status"]["status_str"] == "error"
        assert any("视频输入适配未通过" in str(message) for message in rejected["status"]["messages"])

        interpreted = copy.deepcopy(base)
        interpreted["prompt"]["3"]["inputs"].update(color_policy="fill_missing", assumed_transfer="bt709", assumed_range="tv")
        dis = preview(run("interpreted_dis", interpreted))
        assert len(dis["input_report"]["assumptions"]) == 4 and dis["guide_mode"] == "dis"
        interpreted["prompt"]["2"]["inputs"]["motion_provider"] = "zero"
        zero = preview(run("interpreted_zero", interpreted))
        assert zero["guide_mode"] == "zero" and not zero["guide_cache_hit"]
        assert preview(run("zero_cache_reused", interpreted))["guide_cache_hit"]

        original = copy.deepcopy(base)
        original["prompt"]["1"]["inputs"]["file"] = args.tagged_video
        assert not preview(run("tagged_source_regression", original))["input_report"]["assumptions"]

        full = copy.deepcopy(interpreted)
        full["prompt"]["2"]["inputs"]["motion_provider"] = "dis"
        del full["prompt"]["6"]
        full["prompt"]["7"] = {"class_type":"DLSSExperimentalProcessVideo", "inputs":{
            "sequence":["3",0], "runtime":["4",0], "profile":["5",0], "start_time":0, "duration":0, "scale":100}}
        full["prompt"]["8"] = {"class_type":"SaveVideo", "inputs":{
            "video":["7",0], "filename_prefix":"dlss-input-test/interpreted", "format":"auto", "format.codec":"auto"}}
        output = run("full_video", full)
        assert output["status"]["status_str"] == "success", output["status"]
        print(json.dumps(output["outputs"]["8"], indent=2), flush=True)
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
