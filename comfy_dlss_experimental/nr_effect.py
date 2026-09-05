"""Validated single-look and ordered multi-pass NR effect plans."""
from __future__ import annotations

from copy import deepcopy


STACK_KIND = "nr_pass_stack"
STACK_SCHEMA_VERSION = 3
MAX_NR_PASSES = 3
GUIDE_POLICY = "reuse_input_guides"


def _leaf(profile: object, label: str) -> dict:
    if isinstance(profile, dict) and profile.get("kind") == STACK_KIND:
        raise ValueError("Nested DLSS NR Pass Stacks are not supported")
    if not isinstance(profile, dict) or profile.get("schema_version") != 2:
        raise ValueError(f"{label} must be a current DLSS NR Look (schema 2)")
    return deepcopy(profile)


def build_pass_stack(pass_count: int, pass_1: dict, pass_2: dict | None = None,
                     pass_3: dict | None = None) -> dict:
    """Build a bounded ordered cascade; absent later looks inherit the prior pass."""
    if type(pass_count) is not int or not 1 <= pass_count <= MAX_NR_PASSES:
        raise ValueError(f"pass_count must be an integer from 1 to {MAX_NR_PASSES}")
    supplied = [pass_1, pass_2, pass_3]
    passes = [_leaf(pass_1, "Pass 1")]
    inherited = [False]
    for index in range(1, pass_count):
        candidate = supplied[index]
        if candidate is None:
            passes.append(deepcopy(passes[-1]))
            inherited.append(True)
        else:
            passes.append(_leaf(candidate, f"Pass {index + 1}"))
            inherited.append(False)
    return {
        "schema_version": STACK_SCHEMA_VERSION,
        "kind": STACK_KIND,
        "pass_count": pass_count,
        "guide_policy": GUIDE_POLICY,
        "intermediate_format": "rgba8_raw",
        "passes": passes,
        "inherited": inherited,
    }


def expand_effect(effect: object) -> tuple[list[dict], dict]:
    """Return validated leaf profiles plus stable execution metadata."""
    if not isinstance(effect, dict):
        raise ValueError("NR effect must come from DLSS NR Look or DLSS NR Pass Stack")
    if effect.get("kind") != STACK_KIND:
        return [_leaf(effect, "NR effect")], {
            "kind": "single_pass",
            "pass_count": 1,
            "guide_policy": GUIDE_POLICY,
            "intermediate_format": None,
            "inherited": [False],
        }
    if effect.get("schema_version") != STACK_SCHEMA_VERSION:
        raise ValueError("Recreate DLSS NR Pass Stack with the current node definition")
    count = effect.get("pass_count")
    if type(count) is not int or not 1 <= count <= MAX_NR_PASSES:
        raise ValueError(f"NR Pass Stack supports 1 to {MAX_NR_PASSES} passes")
    if effect.get("guide_policy") != GUIDE_POLICY:
        raise ValueError("Only reuse_input_guides is implemented for multi-pass NR")
    if effect.get("intermediate_format") != "rgba8_raw":
        raise ValueError("Unsupported NR Pass Stack intermediate format")
    raw_passes = effect.get("passes")
    inherited = effect.get("inherited")
    if not isinstance(raw_passes, list) or len(raw_passes) != count:
        raise ValueError("NR Pass Stack pass list does not match pass_count")
    if (not isinstance(inherited, list) or len(inherited) != count
            or any(type(value) is not bool for value in inherited)):
        raise ValueError("NR Pass Stack inheritance metadata is invalid")
    passes = [_leaf(profile, f"Pass {index + 1}") for index, profile in enumerate(raw_passes)]
    return passes, {
        "kind": STACK_KIND,
        "pass_count": count,
        "guide_policy": GUIDE_POLICY,
        "intermediate_format": "rgba8_raw",
        "inherited": list(inherited),
    }
