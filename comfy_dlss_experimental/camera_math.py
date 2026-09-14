"""Explicit calibrated OpenCV camera -> Streamline row-vector convention.

This is a numerical converter, not a camera estimator. Inputs must already
describe the final undistorted VIDEO grid, with one common world/scale per shot.
Supports zero-skew pinhole cameras, including off-center principal points and
different focal scales. K uses edge-origin pixel coordinates (center i+.5).
The full projection, not fabricated jitter, carries the principal-point offset.
"""
from __future__ import annotations

import math

from .camera_provider import validate_projection_depth
from .sl_contract import CameraFrame
from .sr_dimensions import dimensions


def camera_from_opencv(intrinsics, world_to_camera, *, width, height, near, far,
                       depth_inverted=False, previous_intrinsics=None,
                       previous_world_to_camera=None, reset=False):
    import numpy as np
    dimensions(width,height)
    if (type(depth_inverted) is not bool or type(reset) is not bool
            or type(near) not in (int, float) or type(far) not in (int, float)
            or not math.isfinite(near) or not math.isfinite(far) or not 0 < near < far):
        raise ValueError("Invalid explicit camera extent, clip planes or convention")

    def matrices(k, extrinsics):
        try:
            k, extrinsics = np.asarray(k, dtype=np.float64), np.asarray(extrinsics, dtype=np.float64)
        except (ValueError, TypeError) as error:
            raise ValueError("Camera requires numerical K and OpenCV world-to-camera matrices") from error
        if k.shape != (3, 3) or extrinsics.shape not in ((3, 4), (4, 4)) or not np.isfinite(k).all() or not np.isfinite(extrinsics).all():
            raise ValueError("Camera requires finite K[3,3] and world-to-camera[3,4] or [4,4]")
        expected = np.array([[k[0, 0], 0, k[0, 2]], [0, k[1, 1], k[1, 2]], [0, 0, 1]])
        if (k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k, expected, rtol=0, atol=1e-5)
                or not 0 < k[0,2] < width or not 0 < k[1,2] < height):
            raise ValueError("Camera conversion requires zero-skew undistorted pinhole intrinsics with an interior principal point")
        if extrinsics.shape == (3, 4):
            extrinsics = np.vstack((extrinsics, [0, 0, 0, 1]))
        rotation = extrinsics[:3, :3]
        if (not np.allclose(extrinsics[3], [0, 0, 0, 1], atol=1e-7, rtol=0)
                or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-5, rtol=0)
                or not math.isclose(float(np.linalg.det(rotation)), 1, abs_tol=1e-5)):
            raise ValueError("OpenCV world-to-camera must contain a proper rigid rotation, not scale/shear")
        # Flip Y consistently in both the world and camera basis, then transpose
        # for SL row-vector multiplication. OpenCV uses +Y down; SL uses +Y up.
        flip = np.diag([1., -1., 1., 1.])
        view = (flip @ extrinsics @ flip).T
        q = -near / (far - near) if depth_inverted else far / (far - near)
        r = near * far / (far - near) if depth_inverted else -near * far / (far - near)
        projection = np.array([[2 * k[0, 0] / width, 0, 0, 0], [0, 2 * k[1, 1] / height, 0, 0],
                               [2*k[0,2]/width-1, 1-2*k[1,2]/height, q, 1], [0, 0, r, 0]])
        return view, projection, float(k[1, 1])

    view, projection, fy = matrices(intrinsics, world_to_camera)
    if reset:
        previous_view, previous_projection = view, projection
    else:
        if previous_intrinsics is None or previous_world_to_camera is None:
            raise ValueError("Supply the previous camera, or explicitly reset at the first frame/cut")
        previous_view, previous_projection, _ = matrices(previous_intrinsics, previous_world_to_camera)
    inverse_view, inverse_projection = np.linalg.inv(view), np.linalg.inv(projection)
    temporal = inverse_projection @ inverse_view @ previous_view @ previous_projection
    flat = lambda array: tuple(float(v) for v in array.ravel())
    result = CameraFrame(flat(projection), flat(inverse_projection), flat(temporal),
        flat(np.linalg.inv(temporal)), flat(inverse_view[3, :3]), flat(inverse_view[1, :3]),
        flat(inverse_view[0, :3]), flat(inverse_view[2, :3]), near, far, 2 * math.atan(height / (2 * fy)), width / height)
    validate_projection_depth(result, depth_inverted)
    return result
