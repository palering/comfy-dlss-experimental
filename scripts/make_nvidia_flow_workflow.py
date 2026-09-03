#!/usr/bin/env python3
"""Create a NEW UI workflow from an existing NR preview, preserving runtime choices."""
import argparse
import json
from pathlib import Path
import uuid


def build(workflow):
    guides = next(node for node in workflow["nodes"] if node["type"] == "DLSSExperimentalTemporalSettings")
    if any(slot["name"] == "flow_provider" and slot.get("link") is not None for slot in guides["inputs"]):
        raise ValueError("Template already has a connected flow provider")
    identifier = max(node["id"] for node in workflow["nodes"]) + 1
    link = max((item[0] for item in workflow["links"]), default=0) + 1
    guides["inputs"] = [slot for slot in guides["inputs"] if slot["name"] != "flow_provider"]
    slot_index = len(guides["inputs"])
    guides["inputs"].append({"name": "flow_provider", "type": "DLSSE_OPTICAL_FLOW_PROVIDER", "link": link})
    guides["size"] = [max(guides["size"][0], 320), max(guides["size"][1], 230)]
    workflow["nodes"].append({"id": identifier, "type": "DLSSExperimentalNVIDIAFlow",
        "pos": [guides["pos"][0] - 430, guides["pos"][1]], "size": [365, 230],
        "flags": {}, "order": 0, "mode": 0,
        "inputs": [{"name": name, "type": kind, "link": None, "widget": {"name": name}}
                   for name, kind in (("preset", "COMBO"), ("output_grid", "COMBO"), ("temporal_hints", "BOOLEAN"), ("device", "INT"))],
        "outputs": [{"name": "flow_provider", "type": "DLSSE_OPTICAL_FLOW_PROVIDER", "links": [link]},
                    {"name": "settings_json", "type": "STRING", "links": []}],
        "properties": {"Node name for S&R": "DLSSExperimentalNVIDIAFlow"},
        "widgets_values": ["均衡 · Balanced", "4 × 4（较省资源）", True, 0]})
    workflow["links"].append([link, identifier, 0, guides["id"], slot_index, "DLSSE_OPTICAL_FLOW_PROVIDER"])
    workflow.update(id=str(uuid.uuid4()), last_node_id=identifier, last_link_id=link, revision=0)
    for node in workflow["nodes"]:
        node["order"] += 1 if node["id"] != identifier else 0
    return workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    workflow = build(json.loads(args.source.read_text()))
    # Never overwrite a user's existing workflow.
    with args.destination.open("x", encoding="utf-8") as file:
        json.dump(workflow, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
