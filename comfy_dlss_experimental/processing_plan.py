"""Internal feature stages with a lossless adapter for legacy NR effects.

This is not a new public socket or wire protocol. Keep legacy profile validation,
metadata and execution policy intact while both render paths acquire a shared
stage boundary. Parameters are private snapshots and are copied on export.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from .backend_contracts import EXTERNAL_D5V2_NR, OWNED_CNR1_NR, backend_contract
from .nr_effect import expand_effect


@dataclass(frozen=True)
class FeatureStage:
    backend_id: str
    feature: str
    _parameters: dict = field(repr=False)

    def __post_init__(self):
        backend_contract(self.backend_id).require_feature(self.feature)
        if not isinstance(self._parameters, dict):
            raise ValueError("Feature parameters must be a mapping")
        object.__setattr__(self, "_parameters", deepcopy(self._parameters))

    def parameters_copy(self) -> dict:
        return deepcopy(self._parameters)


@dataclass(frozen=True)
class LegacyNRPlan:
    stages: tuple[FeatureStage, ...]
    _metadata: dict = field(repr=False)

    def __post_init__(self):
        if not self.stages or any(
            stage.backend_id != EXTERNAL_D5V2_NR.backend_id or stage.feature != "nr"
            for stage in self.stages
        ):
            raise ValueError("Legacy NR execution requires external D5V2 NR stages")
        object.__setattr__(self, "stages", tuple(self.stages))
        object.__setattr__(self, "_metadata", deepcopy(self._metadata))

    def legacy_parts(self) -> tuple[list[dict], dict]:
        """Keep report shape, bypass handling and per-stage history unchanged."""
        return [stage.parameters_copy() for stage in self.stages], deepcopy(self._metadata)


def compile_legacy_nr(effect: object) -> LegacyNRPlan:
    """Lower existing Look/Stack payloads without probing or executing a backend.

Do not optimize away disabled/zero-mix passes: the legacy executor owns bypass
behavior and report/history semantics. Unsupported Look values still reach the
existing strict profile validator before any Worker invocation.
"""
    profiles, metadata = expand_effect(effect)
    stages = tuple(FeatureStage(EXTERNAL_D5V2_NR.backend_id, "nr", profile) for profile in profiles)
    return LegacyNRPlan(stages, metadata)


def resolve_nr_parts(effect, runtime):
    """Bind existing Look/Stack payloads to the explicitly selected runtime.

    Preserve the legacy report fields; add the resolved backend and wire contract.
    Do not infer that registered capabilities mean a model/GPU has been tested.
    """
    profiles, metadata = compile_legacy_nr(effect).legacy_parts()
    selected = runtime.get("backend", "direct_nr")
    if selected not in {"direct_nr", "owned_nr"}:
        raise ValueError("This NR executor requires direct_nr or owned_nr")
    contract = OWNED_CNR1_NR if selected == "owned_nr" else EXTERNAL_D5V2_NR
    metadata["backend_id"] = contract.backend_id
    return profiles, metadata
