#!/usr/bin/env python3
"""Opt-in parameter sweep on the dedicated GPU Comfy test instance.

Uses preview API node IDs 1..6, existing user-provided runtime files, and a
half-second tagged SDR clip. Never downloads binaries or changes workflows.
Output hashes describe response on this sample, not quality or universal
support. Each run has a private workflow/client identity. No global interrupt.
"""
import argparse
import copy
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid


DEFAULTS = {
    "nr_enabled": True, "intensity": .25, "mix": 1.0,
    "nr_preset": 0, "nr_style": 1, "local_tone_strength": 1.0,
    "local_structure_strength": 1.0, "skin_structure_strength": -1.0,
    "automatic_mask": False, "ui_correction": False, "worker_profile": 1,
}
NATIVE = {
    "intensity": "intensity", "nr_preset": "preset", "nr_style": "style",
    "local_tone_strength": "local_tone", "local_structure_strength": "local_structure",
    "skin_structure_strength": "skin_structure", "automatic_mask": "auto_mask",
    "ui_correction": "ui_correction", "worker_profile": "profile",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8199")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--video", help="Optional Comfy input filename; otherwise keep the supplied workflow input")
    args = parser.parse_args()
    base = json.loads(args.prompt.read_text())
    identity = "nr-look-check-" + uuid.uuid4().hex
    base["client_id"] = identity
    base.setdefault("extra_data", {}).setdefault("extra_pnginfo", {}).setdefault("workflow", {})["id"] = identity
    if args.video:
        base["prompt"]["1"]["inputs"]["file"] = args.video
    base["prompt"]["6"]["inputs"].update(start_time=0, duration=.5, preview_scale=50)
    results = {"run_id": identity, "video": base["prompt"]["1"]["inputs"]["file"], "cases": {}}

    def api(path, data=None):
        request = urllib.request.Request(args.url + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode()) from error

    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    def run(name, payload):
        identifier = api("/prompt", payload)["prompt_id"]
        started = time.monotonic()
        while time.monotonic() - started < 180:
            result = api("/history/" + identifier).get(identifier)
            if result:
                entry = {"prompt_id": identifier, "seconds": round(time.monotonic() - started, 2),
                         "status": result["status"], "outputs": result["outputs"]}
                results["cases"][name] = entry
                save()
                print(name, result["status"]["status_str"], entry["seconds"], flush=True)
                return entry
            time.sleep(.3)
        # Cancel only this script's preview session, never another workflow.
        for session in api("/dlss-experimental/preview/sessions")["sessions"]:
            if session.get("workflow_id") == identity and session["state"] not in ("ready", "failed", "cancelled"):
                api("/dlss-experimental/preview/sessions/" + session["session_id"] + "/cancel", {})
        raise TimeoutError(f"{name}: {identifier}; inspect its history before retrying")

    queue = api("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Dedicated instance is busy; refusing to add a parameter sweep")
    cases = [("legacy", {k: DEFAULTS[k] for k in ("nr_enabled", "intensity", "mix")}),
             ("defaults", DEFAULTS), ("repeat", DEFAULTS)]
    for field, values in (("nr_preset", (1, 2, 3)), ("nr_style", (0, 2)),
                          ("intensity", (0, 2)), ("local_tone_strength", (0, 2)),
                          ("local_structure_strength", (0, 2)), ("skin_structure_strength", (0, 2)),
                          ("automatic_mask", (True,)), ("ui_correction", (True,)),
                          ("worker_profile", (0, 2, 3)), ("mix", (0, .5)), ("nr_enabled", (False,))):
        cases.extend((f"{field}={value}", {**DEFAULTS, field: value}) for value in values)
    failures = []
    try:
        for name, controls in cases:
            payload = copy.deepcopy(base)
            payload["prompt"]["5"]["inputs"] = controls
            entry = run(name, payload)
            if entry["status"]["status_str"] != "success":
                failures.append(name)
                continue
            preview = entry["outputs"]["6"]["dlss_preview"][0]
            report = preview["report"]
            assert preview["frame_count"] == 12 and report["passed"]
            for public, native in NATIVE.items():
                assert report["settings"][native] == controls.get(public, DEFAULTS[public]), (name, public)
            bypass = not controls.get("nr_enabled", True) or controls.get("mix", 1) == 0
            assert report["bypassed"] == bypass
            if bypass:
                assert "cleanup" not in report
            else:
                assert report["cleanup"]["remaining_owned_processes"] == 0
            entry["raw_output_sha256"] = report["raw_output_sha256"]
            entry["guide_cache_hit"] = preview["guide_cache_hit"]

        def digest(name):
            return results["cases"][name].get("raw_output_sha256")

        results["default_output_stable"] = bool(digest("legacy")) and digest("legacy") == digest("defaults") == digest("repeat")
        for name, entry in results["cases"].items():
            entry["same_pixels_as_default"] = entry.get("raw_output_sha256") == digest("defaults") if entry.get("raw_output_sha256") else None
        assert results["default_output_stable"], "Defaults or repeated output differ; do not infer parameter response from hashes"
        assert digest("mix=0") == digest("nr_enabled=False"), "Two bypass paths differ"

        ab = copy.deepcopy(base)
        ab["prompt"]["5"]["inputs"] = {**DEFAULTS, "nr_style": 2, "intensity": 1.5,
            "local_tone_strength": .75, "local_structure_strength": 1.25, "skin_structure_strength": .5}
        ab["prompt"]["9"] = {"class_type": "DLSSExperimentalNRProfile", "inputs": DEFAULTS}
        ab["prompt"]["6"]["inputs"]["profile_a"] = ["9", 0]
        entry = run("two_looks", ab)
        assert entry["status"]["status_str"] == "success", entry["status"]
        preview = entry["outputs"]["6"]["dlss_preview"][0]
        assert preview["profile_a"]["nr_style"] == 1 and preview["profile_b"]["nr_style"] == 2
        assert preview["report"]["settings"]["local_structure"] == 1.25
        assert preview["report"]["cleanup"]["remaining_owned_processes"] == 0

        full = copy.deepcopy(ab)
        del full["prompt"]["6"]
        del full["prompt"]["9"]
        full["prompt"]["7"] = {"class_type": "DLSSExperimentalProcessVideo", "inputs": {
            "sequence": ["3", 0], "runtime": ["4", 0], "profile": ["5", 0],
            "start_time": 0, "duration": 0, "scale": 100}}
        full["prompt"]["8"] = {"class_type": "SaveVideo", "inputs": {
            "video": ["7", 0], "filename_prefix": "dlss-nr-look-test/combined",
            "format": "auto", "format.codec": "auto"}}
        entry = run("full_video", full)
        assert entry["status"]["status_str"] == "success", entry["status"]
        assert entry["outputs"]["8"]
        assert not failures, f"Failed experimental cases: {failures}"
    finally:
        save()
    print("All NR Look checks passed; default output stable:", results["default_output_stable"], flush=True)


if __name__ == "__main__":
    main()
