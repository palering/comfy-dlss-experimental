"""Serializable optical-flow selection; no vendor library imports in Comfy."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlowProvider:
    kind: str = "dis"
    preset: str = "balanced"
    output_grid: int = 4
    device: int = 0
    temporal_hints: bool = True

    def validate(self):
        if self.kind not in ("dis", "nvidia"):
            raise ValueError("Unknown optical-flow provider")
        if self.preset not in ("fast", "balanced", "quality"):
            raise ValueError("Unknown optical-flow preset")
        if type(self.output_grid) is not int or self.output_grid not in (1, 2, 4):
            raise ValueError("Output grid must be 1, 2 or 4")
        if type(self.device) is not int or not 0 <= self.device <= 63:
            raise ValueError("Invalid CUDA device ordinal")
        if type(self.temporal_hints) is not bool:
            raise ValueError("temporal_hints must be boolean")

    @classmethod
    def from_payload(cls, value):
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("Invalid optical-flow provider connection")
        allowed = {"schema_version", "kind", "preset", "output_grid", "device", "temporal_hints"}
        if set(value) - allowed:
            raise ValueError("Unknown optical-flow provider fields")
        result = cls(**{key: item for key, item in value.items() if key != "schema_version"})
        result.validate()
        return result


class DISFlow:
    def __init__(self, config: FlowProvider):
        import cv2
        config.validate()
        preset = {"fast": cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST,
                  "balanced": cv2.DISOPTICAL_FLOW_PRESET_MEDIUM,
                  "quality": cv2.DISOPTICAL_FLOW_PRESET_MEDIUM}[config.preset]
        self.estimator = cv2.DISOpticalFlow_create(preset)
        self.estimator.setUseSpatialPropagation(True)
        self.estimator.setFinestScale(0 if config.preset == "quality" else 1)

    def estimate(self, current, previous):
        return (self.estimator.calc(current, previous, None),
                self.estimator.calc(previous, current, None))

    def reset(self):
        pass  # No previous-flow hint is supplied to DIS.

    def close(self):
        self.estimator = None
