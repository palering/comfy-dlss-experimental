from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


COMPONENT_KINDS = {"sidecar", "reshade", "renodx-dlss5", "dlss", "dlssnr", "adapter"}


@dataclass(frozen=True, slots=True)
class ComponentManifest:
    component_id: str
    kind: str
    version: str
    directory: Path
    files: dict[str, str]
    source_note: str = ""
    compatibility: dict[str, Any] | None = None

    @classmethod
    def load(cls, manifest_path: Path) -> "ComponentManifest":
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if int(raw.get("schema_version", 1)) != 1:
            raise ValueError("Unsupported component manifest schema")
        kind = str(raw["kind"])
        if kind not in COMPONENT_KINDS:
            raise ValueError(f"Unsupported component kind: {kind}")
        files = {str(role): str(path) for role, path in raw.get("files", {}).items()}
        if not files:
            raise ValueError("Component manifest has no files")
        for relative in files.values():
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Component file must stay inside its directory: {relative}")
        return cls(
            component_id=str(raw["id"]),
            kind=kind,
            version=str(raw["version"]),
            directory=manifest_path.parent,
            files=files,
            source_note=str(raw.get("source_note", "")),
            compatibility=dict(raw.get("compatibility", {})),
        )

    def inspect(self) -> dict[str, Any]:
        inspected: dict[str, Any] = {}
        ready = True
        for role, relative in sorted(self.files.items()):
            path = self.directory / relative
            try:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                inspected[role] = {
                    "path": str(path),
                    "sha256": digest.hexdigest(),
                    "size": path.stat().st_size,
                    "present": True,
                }
            except OSError:
                ready = False
                inspected[role] = {"path": str(path), "present": False}
        return {
            "id": self.component_id,
            "kind": self.kind,
            "version": self.version,
            "source_note": self.source_note,
            "compatibility": self.compatibility or {},
            "ready": ready,
            "files": inspected,
        }


def scan_component_manifests(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    components: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not root.is_dir():
        return components, errors
    for manifest_path in sorted(root.glob("*/*/component.json")):
        try:
            components.append(ComponentManifest.load(manifest_path).inspect())
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            errors.append({"path": str(manifest_path), "error": str(exc)})
    return components, errors
