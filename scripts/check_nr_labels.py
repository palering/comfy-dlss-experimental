#!/usr/bin/env python3
"""Opt-in integration check for named choices and legacy numeric API inputs."""
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
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(args.prompt.read_text())
    identity = "nr-labels-check-" + uuid.uuid4().hex
    base["client_id"] = identity
    base.setdefault("extra_data", {}).setdefault("extra_pnginfo", {}).setdefault("workflow", {})["id"] = identity
    base["prompt"]["6"]["inputs"].update(start_time=0, duration=.5, preview_scale=50)
    reports = {}

    def api(path, data=None):
        request = urllib.request.Request(args.url + path, data=None if data is None else json.dumps(data).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    queue = api("/queue")
    assert not queue["queue_running"] and not queue["queue_pending"], "Dedicated instance must be idle"
    definition = api("/object_info/DLSSExperimentalNRProfile")["DLSSExperimentalNRProfile"]
    options = definition["input"]["optional"]
    choices = {name: options[name][1]["options"] for name in ("nr_preset", "nr_style", "worker_profile")}
    assert all(type(v) is str for values in choices.values() for v in values)
    defaults = {"nr_preset": 0, "nr_style": 1, "worker_profile": 1}
    cases = {"numeric": defaults, "named": {k: choices[k][v] for k, v in defaults.items()},
             "cinematic": {k: choices[k][2 if k == "nr_style" else v] for k, v in defaults.items()}}
    try:
        for name, values in cases.items():
            payload = copy.deepcopy(base)
            payload["prompt"]["5"]["inputs"].update(values)
            identifier = api("/prompt", payload)["prompt_id"]
            started = time.monotonic()
            while time.monotonic() - started < 180:
                result = api("/history/" + identifier).get(identifier)
                if result:
                    assert result["status"]["status_str"] == "success", result["status"]
                    report = result["outputs"]["6"]["dlss_preview"][0]["report"]
                    reports[name] = report
                    assert report["settings"]["style"] == (2 if name == "cinematic" else 1)
                    assert report["cleanup"]["remaining_owned_processes"] == 0
                    print(name, "passed", flush=True)
                    break
                time.sleep(.3)
            else:
                for session in api("/dlss-experimental/preview/sessions")["sessions"]:
                    if session.get("workflow_id") == identity and session["state"] in ("created", "preparing", "rendering"):
                        api("/dlss-experimental/preview/sessions/" + session["session_id"] + "/cancel", {})
                raise TimeoutError(identifier)
        assert reports["numeric"]["raw_output_sha256"] == reports["named"]["raw_output_sha256"]
        assert reports["named"]["raw_output_sha256"] != reports["cinematic"]["raw_output_sha256"]
        for invalid in ("unknown", "1", 7, True):
            payload = copy.deepcopy(base)
            payload["prompt"]["5"]["inputs"]["nr_style"] = invalid
            try:
                body = api("/prompt", payload)
            except urllib.error.HTTPError as error:
                assert error.code == 400
                body = json.loads(error.read())
            # Comfy may return 200 for valid independent output nodes (the
            # input inspector) while excluding the invalid NR branch.
            errors = body.get("node_errors", {}).get("5", {}).get("errors", [])
            assert any(e["type"] == "custom_validation_failed" for e in errors), body
        reports["invalid_choices_rejected"] = True
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
