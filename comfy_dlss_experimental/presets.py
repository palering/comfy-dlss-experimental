from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime import format_wine_dll_overrides


REQUIRED_RENO_COMPONENTS = {
    "sidecar",
    "reshade_proxy",
    "renodx_addon",
    "nvngx_dlss",
    "nvngx_dlssnr",
}
REQUIRED_DIRECT_COMPONENTS = {"relay", "worker", "nvngx_dlssnr"}
REQUIRED_OWNED_COMPONENTS = {"worker", "caller", "nvngx_dlssnr"}
REQUIRED_SR_COMPONENTS = {"worker", "nvngx_dlss"}


@dataclass(frozen=True, slots=True)
class RuntimePreset:
    schema_version: int
    preset_id: str
    backend: str
    components: dict[str, str]
    wine_dll_overrides: dict[str, str] = field(default_factory=dict)
    compatibility: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RuntimePreset":
        preset = cls(
            schema_version=int(value.get("schema_version", 1)),
            preset_id=str(value["id"]),
            backend=str(value["backend"]),
            components={str(key): str(path) for key, path in value.get("components", {}).items()},
            wine_dll_overrides={
                str(key): str(mode) for key, mode in value.get("wine_dll_overrides", {}).items()
            },
            compatibility=dict(value.get("compatibility", {})),
        )
        preset.validate()
        return preset

    @classmethod
    def load(cls, path: Path) -> "RuntimePreset":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"Unsupported runtime preset schema: {self.schema_version}")
        if self.backend == "renodx_reshade":
            missing = REQUIRED_RENO_COMPONENTS.difference(self.components)
            if missing:
                raise ValueError(f"Runtime preset is missing components: {', '.join(sorted(missing))}")
        elif self.backend == "direct_nr":
            if set(self.components) != REQUIRED_DIRECT_COMPONENTS:
                raise ValueError("direct_nr requires exactly relay, worker and nvngx_dlssnr components")
            if self.wine_dll_overrides:
                raise ValueError("direct_nr does not load ReShade/Wine override presets")
        elif self.backend == "owned_nr":
            if set(self.components) != REQUIRED_OWNED_COMPONENTS:
                raise ValueError("owned_nr requires exactly worker, caller and nvngx_dlssnr components")
            if self.wine_dll_overrides:
                raise ValueError("owned_nr does not load ReShade/Wine override presets")
        elif self.backend == "owned_sr":
            if set(self.components) != REQUIRED_SR_COMPONENTS:
                raise ValueError("owned_sr requires exactly worker and nvngx_dlss; the NR caller shim is not used")
            if self.wine_dll_overrides:
                raise ValueError("owned_sr does not load ReShade/Wine override presets")
            from .sr_runtime import project_id
            project_id(self.compatibility.get("project_id"))
        elif self.backend == "owned_sl":
            from .sl_runtime import SL_COMPONENTS, project_id
            if set(self.components) != set(SL_COMPONENTS):
                raise ValueError("owned_sl requires the CXR1 Worker and exactly the seven SR/RR runtime DLL roles")
            if self.wine_dll_overrides:
                raise ValueError("owned_sl does not load ReShade/Wine override presets")
            project_id(self.compatibility.get("project_id"))
        elif self.backend != "nvidia_official":
            raise ValueError(f"Unknown backend: {self.backend}")
        format_wine_dll_overrides(self.wine_dll_overrides)


def resolve_component_paths(preset: RuntimePreset, *, base_dir: Path) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for name, raw_path in preset.components.items():
        if name in {"relay", "worker", "caller"} and raw_path == f"@bundled/{name}":
            if name in {"worker", "caller"} and preset.backend != "owned_nr":
                # SR requires a separately built SDK-capable Worker. Existing
                # helper bundles do not promise that capability.
                if preset.backend == "owned_sr":
                    raise ValueError("SR requires an explicit SDK-enabled Worker path; the bundled NR helper does not include the SR SDK")
                if preset.backend == "owned_sl":
                    raise ValueError("owned_sl requires an explicit CXR1 Worker path; bundled NR helpers are not interchangeable")
                raise ValueError("Bundled Worker/caller use CNR1; select an owned_nr preset, not the D5V2 backend")
            from .helper_artifacts import find_helper
            resolved[name] = find_helper(name)
            continue
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        resolved[name] = path.resolve(strict=False)
    return resolved


def preset_fingerprint(preset: RuntimePreset, *, base_dir: Path) -> tuple[str, dict[str, str]]:
    """Hash preset metadata and component bytes; missing files remain visible in the key."""

    component_hashes: dict[str, str] = {}
    for name, path in sorted(resolve_component_paths(preset, base_dir=base_dir).items()):
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            component_hashes[name] = digest.hexdigest()
        except OSError:
            component_hashes[name] = f"missing:{path}"

    payload = {
        "schema_version": preset.schema_version,
        "id": preset.preset_id,
        "backend": preset.backend,
        "wine_dll_overrides": preset.wine_dll_overrides,
        "compatibility": preset.compatibility,
        "components": component_hashes,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), component_hashes


def execution_fingerprint(
    preset_key: str,
    *,
    platform_name: str,
    proton_selection: str | None,
    display_backend: str,
    execution_mode: str | None = None,
) -> str:
    payload = {
        "preset_key": preset_key,
        "platform": platform_name.lower(),
        "proton": proton_selection,
        "display_backend": display_backend,
    }
    if execution_mode is not None:
        payload["execution_mode"] = execution_mode
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
