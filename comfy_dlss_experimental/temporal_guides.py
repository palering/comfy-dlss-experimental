"""Provider-independent guide preparation, separate from NGX/Windows processes.

Motion is current-to-previous in full-resolution pixel units, top-left origin.
Dependencies are imported only when a guide generator is constructed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from .flow_provider import FlowProvider
from .guide_providers import create_flow_estimator


@dataclass(frozen=True)
class GuideSettings:
    analysis_scale: float = 0.5
    scene_cut_threshold: float = 0.35
    consistency_pixels: float = 2.5
    motion_provider: str = "dis"
    flow: FlowProvider | None = None
    external_motion: dict | None = None

    def validate(self):
        if self.motion_provider not in ("dis", "zero", "nvidia", "external"):
            raise ValueError("Unsupported motion provider")
        if self.motion_provider == "external":
            if not isinstance(self.external_motion, dict) or self.flow is not None:
                raise ValueError("External motion requires its own manifest configuration, not an estimator")
        elif self.external_motion is not None:
            raise ValueError("External motion configuration requires external mode")
        if self.flow is not None:
            if not isinstance(self.flow, FlowProvider):
                raise ValueError("Invalid flow provider configuration")
            self.flow.validate()
            if self.flow.kind != self.motion_provider:
                raise ValueError("Flow provider and motion mode disagree")
        for name, low, high in (("analysis_scale", 0.25, 1), ("scene_cut_threshold", 0.01, 1),
                                ("consistency_pixels", 0.1, 20)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid {name}")


class TemporalGuideGenerator:
    def __init__(self, width: int, height: int, settings: GuideSettings = GuideSettings(), *, cancelled=lambda: False,
                 source_identity=None):
        settings.validate()
        if not 64 <= width <= 7680 or not 64 <= height <= 4320:
            raise ValueError("unsupported guide dimensions")
        import cv2
        import numpy as np
        self.cv, self.np = cv2, np
        self.width, self.height, self.settings = width, height, settings
        self.small_width = max(64, round(width * settings.analysis_scale))
        self.small_height = max(64, round(height * settings.analysis_scale))
        self.previous = None
        self.estimator = None
        self.external = None
        self.closed = False
        yy, xx = np.mgrid[:self.small_height, :self.small_width].astype(np.float32)
        self.xx, self.yy = xx, yy
        if settings.motion_provider == "external":
            from .external_motion import ExternalMotionReader
            self.external = ExternalMotionReader(settings.external_motion, width, height, source_identity)
        else:
            self.estimator = create_flow_estimator(
                settings.motion_provider, self.small_width, self.small_height,
                settings.flow, cancelled=cancelled,
            )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        if self.estimator is not None:
            self.estimator.close()
        self.closed = True

    def process(self, rgba: bytes, *, force_reset: bool = False, pts_ns=None) -> tuple[bytes, dict]:
        if self.closed:
            raise RuntimeError("Guide generator is closed")
        cv, np = self.cv, self.np
        if len(rgba) != self.width * self.height * 4:
            raise ValueError("guide input must be tightly packed RGBA8")
        image = np.frombuffer(rgba, np.uint8).reshape(self.height, self.width, 4)
        gray = cv.cvtColor(image, cv.COLOR_RGBA2GRAY)
        current = cv.resize(gray, (self.small_width, self.small_height), interpolation=cv.INTER_AREA)
        first = self.previous is None
        external_motion, external_info = self.external.read(pts_ns) if self.external is not None else (None, {})
        force_reset = force_reset or bool(external_info.get("reset"))
        scene_score = 0.0 if first else float(cv.absdiff(current, self.previous).mean()) / 255
        cut = not first and scene_score >= self.settings.scene_cut_threshold
        reset = first or force_reset or cut
        metrics = {"reset": reset, "reset_reason": "first" if first else "explicit" if force_reset else "scene_cut" if cut else None,
                   "scene_score": scene_score, "motion_p95_pixels": 0.0, "consistent_fraction": 1.0,
                   "unwarped_mae": None, "warped_mae": None, "negated_flow_mae": None}
        metrics["motion_provider"] = self.settings.motion_provider
        metrics["analysis_width"], metrics["analysis_height"] = self.small_width, self.small_height
        if self.external is not None:
            metrics["external_guide"] = external_info
        if reset and self.estimator is not None:
            self.estimator.reset()
        if reset or self.settings.motion_provider == "zero":
            motion = np.zeros((self.height, self.width, 2), np.float32)
            if not reset:
                metrics["consistent_fraction"] = None  # No estimation was performed.
        elif self.external is not None:
            motion = external_motion
            # No backward/forward consistency claim without a reverse field.
            metrics["consistent_fraction"] = None
            metrics["motion_p95_pixels"] = float(np.percentile(np.linalg.norm(motion, axis=2), 95))
        else:
            backward, forward = self.estimator.estimate(current, self.previous)
            expected = (self.small_height, self.small_width, 2)
            if backward.shape != expected or forward.shape != expected:
                raise ValueError("optical flow provider returned wrong dimensions")
            if not np.isfinite(backward).all() or not np.isfinite(forward).all():
                raise ValueError("non-finite optical flow")
            sx, sy = self.xx + backward[..., 0], self.yy + backward[..., 1]
            inside = (sx >= 0) & (sy >= 0) & (sx <= self.small_width - 1) & (sy <= self.small_height - 1)
            reverse = cv.remap(forward, sx, sy, cv.INTER_LINEAR, borderMode=cv.BORDER_CONSTANT)
            consistency = np.linalg.norm(backward + reverse, axis=2)
            # Diagnostics only: no reactive-mask input exists in this worker's ABI.
            # Do not silently damp vectors in occluded regions or claim a mask is used.
            valid = inside & (consistency < self.settings.consistency_pixels)
            warped = cv.remap(self.previous, sx, sy, cv.INTER_LINEAR, borderMode=cv.BORDER_REFLECT)
            wrong = cv.remap(self.previous, self.xx - backward[..., 0], self.yy - backward[..., 1],
                             cv.INTER_LINEAR, borderMode=cv.BORDER_REFLECT)
            metrics["consistent_fraction"] = float(valid.mean())
            if inside.any():
                # Identical region for both signs; these are guide diagnostics, not perceptual scores.
                metrics["unwarped_mae"] = float(cv.absdiff(current, self.previous)[inside].mean())
                metrics["warped_mae"] = float(cv.absdiff(current, warped)[inside].mean())
                metrics["negated_flow_mae"] = float(cv.absdiff(current, wrong)[inside].mean())
            motion = cv.resize(backward, (self.width, self.height), interpolation=cv.INTER_LINEAR)
            motion[..., 0] *= self.width / self.small_width
            motion[..., 1] *= self.height / self.small_height
            if np.abs(motion).max() > 65504:
                raise ValueError("motion exceeds float16 range")
            metrics["motion_p95_pixels"] = float(np.percentile(np.linalg.norm(motion, axis=2), 95))
        self.previous = current
        return np.ascontiguousarray(motion, dtype="<f2").tobytes(), metrics
