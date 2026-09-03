from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ProtonInstallation:
    """One usable Proton wrapper found in a launcher-owned directory."""

    source: str
    name: str
    version: str
    root: Path
    executable: Path

    @property
    def selection_id(self) -> str:
        return f"{self.name} | {self.version} | {self.source} | {self.executable}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "name": self.name,
            "version": self.version,
            "root": str(self.root),
            "executable": str(self.executable),
            "selection_id": self.selection_id,
        }


@dataclass(slots=True)
class ProbeReport:
    platform: str
    architecture: str
    supported_host: bool
    python_version: str
    protons: list[ProtonInstallation] = field(default_factory=list)
    tools: dict[str, Any] = field(default_factory=dict)
    gpu: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["protons"] = [item.to_dict() for item in self.protons]
        return result
