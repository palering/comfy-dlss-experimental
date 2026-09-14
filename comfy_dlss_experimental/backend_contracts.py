"""Static backend contracts, not installed-runtime or GPU capability probes.

Describe only implemented wire behavior. Future backends need distinct IDs and
their own contracts; registering a DLL path must never imply feature support.
This module deliberately imports no platform, media or vendor libraries.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class PlaneContract:
    role: str
    format: str
    semantics: str


@dataclass(frozen=True)
class BackendContract:
    backend_id: str
    protocol: str
    features: frozenset[str]
    required_planes: tuple[PlaneContract, ...]
    output_format: str
    spatial_relation: str
    temporal_relation: str

    def require_feature(self, feature: str) -> None:
        if not isinstance(feature, str) or feature not in self.features:
            raise ValueError(f"Backend {self.backend_id} does not implement {feature}")


EXTERNAL_D5V2_NR = BackendContract(
    backend_id="external_d5v2_nr",
    protocol="D5V2",
    features=frozenset({"nr"}),
    required_planes=(
        PlaneContract("color", "rgba8", "tightly_packed_working_color"),
        PlaneContract("motion", "rg16f_le", "current_to_previous_pixels_top_left_xy"),
    ),
    output_format="rgba8",
    spatial_relation="same_extent",
    temporal_relation="one_to_one_preserve_pts",
)

OWNED_CNR1_NR = BackendContract(
    backend_id="owned_cnr1_nr",
    protocol="CNR1",
    features=frozenset({"nr"}),
    required_planes=(
        PlaneContract("color", "rgba16f_le", "tightly_packed_working_color_no_implicit_eotf"),
        PlaneContract("motion", "rg16f_le", "current_to_previous_pixels_top_left_xy"),
    ),
    output_format="rgba16f_le",
    spatial_relation="same_extent",
    temporal_relation="one_to_one_preserve_pts",
)

# Describes the implemented optional SDK build's wire contract, not whether the
# user's installed Worker was compiled with that SDK or the GPU can run it.
OWNED_CSR1_SR = BackendContract(
    backend_id="owned_csr1_sr", protocol="CSR1", features=frozenset({"sr", "dlaa"}),
    required_planes=(
        PlaneContract("color", "rgba16f_le", "declared_working_color"),
        PlaneContract("motion", "rg16f_le", "current_to_previous_pixels_top_left_xy"),
        PlaneContract("depth", "r32f_le", "device_depth_zero_to_one"),
    ),
    output_format="rgba16f_le", spatial_relation="explicit_output_extent",
    temporal_relation="one_to_one_preserve_pts",
)

OWNED_CXR1_SL = BackendContract(
    backend_id="owned_cxr1_sl", protocol="CXR1", features=frozenset({"sr", "dlaa", "rr", "rr_dlaa"}),
    required_planes=(
        PlaneContract("color", "rgba16f_le", "feature_specific_sdr_working_color"),
        PlaneContract("motion", "rg16f_le", "current_to_previous_pixels_top_left_xy"),
        PlaneContract("depth", "r32f_le", "device_depth_zero_to_one"),
    ),
    output_format="rgba16f_le", spatial_relation="explicit_output_extent",
    temporal_relation="one_to_one_preserve_pts",
)
# CXR1 also requires explicit camera/frame constants. RR adds material planes
# and world/view transforms; feature_requirements resolves those consumers.

# Registration describes wire behavior only. Legacy lowering/runtime presets
# continue selecting EXTERNAL_D5V2_NR; no automatic backend substitution.
_CONTRACTS = MappingProxyType({c.backend_id: c for c in (EXTERNAL_D5V2_NR, OWNED_CNR1_NR, OWNED_CSR1_SR, OWNED_CXR1_SL)})


def backend_contract(backend_id: str) -> BackendContract:
    """Resolve a known implementation; unknown IDs never fall back to NR."""
    if not isinstance(backend_id, str) or backend_id not in _CONTRACTS:
        raise ValueError(f"Unknown DLSS backend: {backend_id!r}")
    return _CONTRACTS[backend_id]
