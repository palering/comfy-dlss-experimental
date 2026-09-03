#!/usr/bin/env python3
"""Opt-in: verify >30s preview and SaveVideo on an idle dedicated Comfy server."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8199")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--base-dir", type=Path, required=True, help="Dedicated Comfy base directory")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    def api(path, value=None):
        request = urllib.request.Request(args.url + path, data=json.dumps(value).encode() if value is not None else None,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    def idle():
        queue = api("/queue")
        status = api("/dlss-experimental/executions")
        assert not queue["queue_running"] and not queue["queue_pending"] and not status["active"] and not status["resident"]["workers"], "Dedicated instance is not idle"
    idle()
    identity = "dlss-full-duration-" + uuid.uuid4().hex
    video = args.base_dir / "input" / (identity + ".mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i", "testsrc2=size=128x96:rate=24:duration=36",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=36",
        "-vf", "setparams=range=limited:color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709",
        "-c:v", "libx264", "-threads", "2",
        "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "iec61966-2-1",
        "-color_range", "tv", "-c:a", "aac", "-t", "36", str(video)], check=True, timeout=40)
    base = json.loads(args.prompt.read_text())
    base["prompt"]["1"]["inputs"]["file"] = video.name
    base["prompt"]["4"]["inputs"].update(keep_worker_alive=False, worker_policy="isolated")
    base["prompt"]["9"] = {"class_type": "DLSSExperimentalNVIDIAFlow", "inputs": {
        "preset": "均衡 · Balanced", "output_grid": "4 × 4（较省资源）", "temporal_hints": True, "device": 0}}
    base["prompt"]["2"]["inputs"]["flow_provider"] = ["9", 0]
    results = {"fixture": str(video)}
    def run(name, payload):
        idle()
        payload["client_id"] = identity + name
        payload["extra_data"]["extra_pnginfo"]["workflow"]["id"] = identity + name
        prompt_id = api("/prompt", payload)["prompt_id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = api("/history/" + prompt_id).get(prompt_id)
            if result:
                results[name] = result
                args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))
                if result["status"]["status_str"] != "success":
                    errors = [body.get("exception_message", name) for event, body in result["status"]["messages"] if event == "execution_error"]
                    raise RuntimeError("; ".join(errors) or "Execution failed; see report")
                return result
            time.sleep(.2)
        raise TimeoutError(name + "; test sends no global interruption")
    for name, to_end, start, duration, mode, cursor, expected in (
        ("full_preview", True, 0, 2, "range", 0, 864),
        ("manual_range", False, 30, 4, "range", 0, 96),
        ("full_window_single_frame", True, 0, 2, "frame", 35, 1),
    ):
        payload = copy.deepcopy(base)
        payload["prompt"]["6"]["inputs"].update(process_to_end=to_end, start_time=start, duration=duration,
            preview_mode=mode, cursor_time=cursor, preview_scale=100)
        session = run(name, payload)["outputs"]["6"]["dlss_preview"][0]
        assert session["frame_count"] == expected, session
        assert session["report"]["cleanup"]["remaining_owned_processes"] == 0
        if to_end:
            assert session["range_duration"] == 36
        print(json.dumps({"case": name, "output_frames": session["frame_count"], "seconds": session["duration"],
            "elapsed": session["execution"]["elapsed_seconds"]}), flush=True)
    payload = copy.deepcopy(base)
    del payload["prompt"]["6"]
    payload["prompt"]["7"] = {"class_type": "DLSSExperimentalProcessVideo", "inputs": {
        "sequence": ["3", 0], "runtime": ["4", 0], "profile": ["5", 0], "start_time": 0,
        "duration": 2, "scale": 100, "process_to_end": True}}
    payload["prompt"]["8"] = {"class_type": "SaveVideo", "inputs": {"video": ["7", 0],
        "filename_prefix": "dlss-test/" + identity, "format": "auto", "format.codec": "auto"}}
    run("process_save_full", payload)
    outputs = list((args.base_dir / "output" / "dlss-test").glob(identity + "*.mp4"))
    assert len(outputs) == 1, outputs
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(outputs[0])],
                            capture_output=True, text=True, check=True, timeout=30)
    streams = json.loads(probe.stdout)["streams"]
    stream = next(s for s in streams if s["codec_type"] == "video")
    assert int(stream["nb_read_frames"]) == 864 and abs(float(stream["duration"]) - 36) < .05, stream
    assert (stream["width"], stream["height"]) == (128, 96)
    assert any(s["codec_type"] == "audio" for s in streams)
    idle()
    results.update(passed=True, saved_output=str(outputs[0]), encoded_stream=stream)
    args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print("PASS: 36s preview, independent manual range, frame at 35s, and full 36s SaveVideo with audio.", flush=True)


if __name__ == "__main__":
    main()
