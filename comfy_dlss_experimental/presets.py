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
        elif self.backend != "nvidia_official":
            raise ValueError(f"Unknown backend: {self.backend}")
        format_wine_dll_overrides(self.wine_dll_overrides)


def resolve_component_paths(preset: RuntimePreset, *, base_dir: Path) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for name, raw_path in preset.components.items():
        if raw_path == "@bundled/relay" and name == "relay":
            from .helper_artifacts import find_helper
            resolved[name] = find_helper("relay")
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
