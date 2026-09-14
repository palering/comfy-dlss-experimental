"""Read external numerical motion by source PTS, then adapt spatial pixel units.

No flow estimator or depth model is instantiated. Only a requested frame lives
in memory. Resizing a motion grid scales X/Y displacement in the same operation;
changing temporal direction, crop, source identity or jitter is not guessed.
"""
from __future__ import annotations

from .external_guides import reopen_external_guide


class ExternalMotionReader:
    def __init__(self, config, width, height, source_identity):
        self.provider = reopen_external_guide(config)
        self.provider.validate_nr_motion()
        if not isinstance(source_identity, dict):
            raise ValueError("External motion requires the decoded source identity")
        if (source_identity.get("sha256") != self.provider.source_sha256
                or source_identity.get("width") != self.provider.source_width
                or source_identity.get("height") != self.provider.source_height):
            raise ValueError("External motion source content/dimensions mismatch; rebuild guides for this VIDEO")
        if type(width) is not int or type(height) is not int or not (64 <= width <= 7680 and 64 <= height <= 4320):
            raise ValueError("External motion output dimensions exceed the guide limits")
        self.width, self.height = width, height
        self.last_pts = None

    def read(self, pts_ns):
        import numpy as np
        if type(pts_ns) is not int or pts_ns < 0 or (self.last_pts is not None and pts_ns <= self.last_pts):
            raise ValueError("External motion requires increasing source-relative PTS")
        values, info = self.provider.read_at(pts_ns, source_sha256=self.provider.source_sha256)
        values = np.asarray(values, dtype=np.float32)
        source_width, source_height = self.provider.width, self.provider.height
        if values.shape != (source_height, source_width, 2) or not np.isfinite(values).all():
            raise ValueError("External motion must be a finite dense XY numerical field")
        if (self.width, self.height) != (source_width, source_height):
            import cv2
            values = cv2.resize(values, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
            values[..., 0] *= self.width / source_width
            values[..., 1] *= self.height / source_height
        if np.abs(values).max() > 65504:
            raise ValueError("External motion exceeds the NR RG16F range")
        self.last_pts = pts_ns
        return values, {**info, "cache_identity": self.provider.cache_identity,
                        "source_grid": [source_width, source_height],
                        "output_grid": [self.width, self.height],
                        "conversion": "resize_and_scale_pixels" if (self.width, self.height) != (source_width, source_height) else "none"}
