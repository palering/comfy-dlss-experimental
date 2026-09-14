"""Resolved input requirements, separate from runtime/GPU availability.

This table describes implemented consumers, not every option in vendor SDKs.
An accepted but unconsumed attachment must never be labelled an enhancement.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InputRequirement:
    role: str
    usage: str
    semantics: str


def input_requirements(backend_id, feature):
    nr = backend_id in {"external_d5v2_nr", "owned_cnr1_nr"} and feature == "nr"
    csr = backend_id == "owned_csr1_sr" and feature in {"sr", "dlaa"}
    sl = backend_id == "owned_cxr1_sl" and feature in {"sr", "dlaa", "rr", "rr_dlaa"}
    if not (nr or csr or sl):
        raise ValueError("No implemented input requirements for this feature/backend")
    rr = sl and feature in {"rr", "rr_dlaa"}
    required = {"color", "motion", "timeline", "frame_metadata"}
    if not nr:
        required.add("depth")
    if sl:
        required.add("camera")
    if rr:
        required.update({"diffuse_albedo", "specular_albedo", "normal_roughness", "specular_motion", "world_view"})
    meanings = {
        "color": "linear noisy renderer color" if rr else "declared SDR working color",
        "motion": "current-to-previous full-input pixel XY; includes camera motion; excludes sampling jitter",
        "timeline": "source-bound frame PTS and exact selected range",
        "frame_metadata": "per-frame history reset and feature-specific jitter/exposure parameters",
        "depth": "device_z in [0,1], calibrated to the selected projection",
        "camera": "explicit unjittered projection, world-space pose and temporal transforms",
        "diffuse_albedo": "linear diffuse reflectance",
        "specular_albedo": "linear specular reflectance",
        "normal_roughness": "normalized shading normal XYZ and linear roughness",
        "specular_motion": "reflection motion; hit-distance alternative is not implemented here",
        "world_view": "mutually inverse world/view matrices",
        "normals": "standalone normals attachment; no implemented consumer in this path",
        "mask": "standalone mask attachment; no implemented consumer in this path",
        "confidence": "standalone confidence attachment; no implemented consumer in this path",
        "audio": "optional host-side aligned audio export; not a DLSS model input",
    }
    return tuple(InputRequirement(role, "required" if role in required else "optional_consumed" if role == "audio"
                                  else "not_consumed", meaning) for role, meaning in meanings.items())


def requirements_report(backend_id, feature, available=()):
    requirements = input_requirements(backend_id, feature)
    available = set(available)
    unknown = available - {item.role for item in requirements}
    if unknown:
        raise ValueError("Unknown input roles: " + ", ".join(sorted(unknown)))
    missing = [item.role for item in requirements if item.usage == "required" and item.role not in available]
    return {"backend_id": backend_id, "feature": feature, "missing_required": missing,
        "declared_inputs_complete": not missing, "gpu_validated": False,
        "inputs": [{"role": item.role, "usage": item.usage, "available": item.role in available,
                    "semantics": item.semantics} for item in requirements],
        "unconsumed": [item.role for item in requirements if item.usage == "not_consumed" and item.role in available],
        "note": "Requirements describe this implementation; data availability is not correctness or GPU acceptance."}
