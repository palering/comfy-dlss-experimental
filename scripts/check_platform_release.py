"""Opt-in isolated Comfy test: old graph, manual Proton, installed relay, rejection."""
import argparse
import copy
import json
from pathlib import Path
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8199")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--proton-path", required=True, help="Installed Proton directory or launcher on the test host")
    args = parser.parse_args()
    def api(path, data=None):
        request = urllib.request.Request(args.url + path, headers={"Content-Type": "application/json"},
            data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    queue = api("/queue")
    assert not queue["queue_running"] and not queue["queue_pending"]
    assert api("/dlss-experimental/host")["platform"] == "linux"
    definitions = api("/object_info/DLSSExperimentalRuntimeConfig")["DLSSExperimentalRuntimeConfig"]
    assert {"system_platform", "linux_execution", "proton_path"} <= set(definitions["input"]["optional"])
    base = {"prompt": json.loads(args.prompt.read_text())["prompt"], "client_id": "dlss-platform-check"}
    base["prompt"]["6"]["inputs"].update(preview_mode="frame", cursor_time=1, duration=2)
    results = {}
    def run(name, payload):
        identifier = api("/prompt", payload)["prompt_id"]
        started = time.monotonic()
        while time.monotonic() - started < 150:
            record = api("/history/" + identifier).get(identifier)
            if record:
                results[name] = record
                print(name, record["status"]["status_str"], round(time.monotonic() - started, 2), flush=True)
                return record
            time.sleep(.2)
        raise TimeoutError(name)
    try:
        legacy = run("legacy", copy.deepcopy(base))
        assert legacy["status"]["status_str"] == "success", legacy["status"]
        assert legacy["outputs"]["6"]["dlss_preview"][0]["report"]["passed"]
        original_preset = Path(base["prompt"]["4"]["inputs"]["runtime_preset"])
        preset = json.loads(original_preset.read_text())
        # Keep the user's preset unchanged; materialize relative external paths.
        for role, raw in preset["components"].items():
            path = Path(raw).expanduser()
            preset["components"][role] = str(path if path.is_absolute() else (original_preset.parent / path).resolve())
        preset["components"]["relay"] = "@bundled/relay"
        preset_path = args.report.with_name("platform-release-test-preset.json")
        preset_path.write_text(json.dumps(preset, indent=2), encoding="utf-8")
        custom = copy.deepcopy(base)
        runtime = custom["prompt"]["4"]["inputs"]
        runtime.update(system_platform="linux", linux_execution="proton", proton="saved-but-no-longer-installed",
                       proton_path=args.proton_path,
                       runtime_preset=str(preset_path))
        custom["prompt"]["9"] = {"class_type": "DLSSExperimentalNVIDIAFlow", "inputs": {
            "preset": "均衡 · Balanced", "output_grid": "4 × 4（较省资源）", "device": 0, "temporal_hints": True}}
        custom["prompt"]["2"]["inputs"]["flow_provider"] = ["9", 0]
        result = run("custom_proton_installed_helpers", custom)
        assert result["status"]["status_str"] == "success", result["status"]
        preview = result["outputs"]["6"]["dlss_preview"][0]
        assert preview["report"]["passed"] and preview["guide_mode"] == "nvidia"
        for name, settings, message in (
            ("reserved_native", {"linux_execution": "native_reserved"}, "尚未实现"),
            ("wrong_host", {"system_platform": "windows"}, "不一致"),
        ):
            invalid = copy.deepcopy(custom)
            invalid["prompt"]["4"]["inputs"].update(settings)
            result = run(name, invalid)
            assert message in json.dumps(result, ensure_ascii=False), result["status"]
            assert not api("/dlss-experimental/executions")["active"]
    finally:
        args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
