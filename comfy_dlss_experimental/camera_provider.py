"""Hash-bound camera timelines. No pose estimator, guessed camera or ML imports."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, fields, replace
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

from .sl_bundle import MAX_BUNDLE_FRAMES, MAX_MANIFEST_BYTES, _integer, _object
from .sl_contract import CameraFrame, ReconstructionSettings, SLFrameMetadata
from .sr_dimensions import dimensions, LIMITS


@dataclass(frozen=True)
class CameraRecord:
    pts_ns: int
    camera: CameraFrame
    metadata: SLFrameMetadata


@dataclass(frozen=True)
class CameraProvider:
    manifest_path: Path
    manifest_sha256: str
    source_sha256: str
    width: int
    height: int
    fps: str
    depth_inverted: bool
    provenance: str
    frames: tuple[CameraRecord, ...] = field(repr=False)
    source_video: object = field(default=None, repr=False, compare=False)
    _timestamps: tuple[int, ...] = field(init=False, repr=False)
    role: str = field(default="camera", init=False)
    semantics: str = field(default="streamline_explicit_camera", init=False)

    def __post_init__(self):
        object.__setattr__(self, "_timestamps", tuple(frame.pts_ns for frame in self.frames))

    def __deepcopy__(self, memo):
        return self

    def index_at(self, pts_ns):
        _integer(pts_ns, "camera requested PTS", 0, (1 << 63) - 1)
        first, after = bisect_left(self._timestamps, pts_ns - 1000), bisect_right(self._timestamps, pts_ns + 1000)
        if after - first != 1:
            raise ValueError("Camera has no unique matching source PTS; interpolation is not implicit")
        return first

    def reopen(self, cancelled=lambda: False):
        value = load_camera_manifest(self.manifest_path, cancelled=cancelled)
        if value.manifest_sha256 != self.manifest_sha256:
            raise ValueError("Camera manifest changed after input assembly; rerun Camera Input")
        return replace(value, source_video=self.source_video)

    def to_attachment(self):
        from .media_pipeline import GuideAttachment
        return GuideAttachment(self.role, self.source_video, self.semantics, self)

    def report(self):
        return {"kind": "camera_timeline", "schema_version": 1, "source_sha256": self.source_sha256,
            "source_size": [self.width, self.height], "view_id": "source", "fps": self.fps,
            "time_origin": "video_stream_start", "frames": len(self.frames), "depth_inverted": self.depth_inverted,
            "manifest_sha256": self.manifest_sha256, "provenance": self.provenance,
            "jitter_policy": "unjittered_video", "exposure_policy": "sl_sr_auto_no_preexposure",
            "validation": "declared camera contract; no accuracy or reprojection quality claim"}


def load_camera_manifest(manifest_path, *, cancelled=lambda: False):
    path = Path(manifest_path).expanduser().resolve(strict=True)
    if cancelled():
        raise InterruptedError("Camera input cancelled")
    with path.open("rb") as file:
        raw = file.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Camera manifest exceeds 32 MiB")
    value = json.loads(raw, object_pairs_hook=_object)
    expected = {"schema_version", "kind", "source", "fps", "depth_inverted", "provenance", "frames"}
    if (not isinstance(value, dict) or set(value) != expected or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["kind"] != "dlss_camera_timeline"):
        raise ValueError("Expected an explicit version-1 dlss_camera_timeline")
    source = value["source"]
    if (not isinstance(source, dict) or set(source) != {"sha256", "view_id", "width", "height", "time_origin"}
            or source["view_id"] != "source" or source["time_origin"] != "video_stream_start"):
        raise ValueError("Camera timeline must bind the unmodified source view and video stream time origin")
    digest = source["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Camera source needs an explicit lowercase SHA-256")
    width = _integer(source["width"], "camera width", LIMITS['min_side'], LIMITS['input_max_side'])
    height = _integer(source["height"], "camera height", LIMITS['min_side'], LIMITS['input_max_side'])
    dimensions(width,height)
    if (type(value["depth_inverted"]) is not bool or not isinstance(value["provenance"], str)
            or value["provenance"] not in {"renderer", "calibrated", "estimated", "synthetic_test"}):
        raise ValueError("Declare camera depth convention and renderer/calibrated/estimated/synthetic_test provenance")
    if not isinstance(value["fps"], str) or len(value["fps"]) > 32:
        raise ValueError("Camera fps must be a bounded rational string")
    try:
        rate = Fraction(value["fps"])
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("Invalid camera fps") from error
    if not 1 <= rate <= 120:
        raise ValueError("Camera fps must be 1..120")
    entries = value["frames"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BUNDLE_FRAMES:
        raise ValueError("Camera timeline requires 1..100000 source frames")
    result = []
    settings = ReconstructionSettings(width, height, width, height, mode="dlaa", depth_inverted=value["depth_inverted"])
    for index, entry in enumerate(entries):
        if cancelled():
            raise InterruptedError("Camera input cancelled")
        if not isinstance(entry, dict) or set(entry) != {"pts_ns", "camera", "metadata"}:
            raise ValueError("Each camera frame requires explicit PTS, camera and frame metadata")
        pts = _integer(entry["pts_ns"], "camera PTS", 0, (1 << 63) - 1)
        if abs(pts - round(index * 1_000_000_000 / rate)) > 1000:
            raise ValueError("Camera timeline must contain every CFR source frame from zero; do not relabel VFR")
        camera = CameraFrame.from_payload(entry["camera"])
        camera.validate_extent(width, height)
        validate_projection_depth(camera, value["depth_inverted"])
        metadata = entry["metadata"]
        if not isinstance(metadata, dict) or set(metadata) != {f.name for f in fields(SLFrameMetadata)}:
            raise ValueError("Camera timeline requires all explicit frame metadata fields")
        metadata = SLFrameMetadata(**metadata)
        metadata.validate_for(settings)
        if any(getattr(metadata, key) != 1 for key in ("motion_scale_x", "motion_scale_y", "pre_exposure", "exposure_scale", "exposure")):
            raise ValueError("VIDEO Streamline input requires pixel motion and no renderer pre-exposure; scales must be 1")
        # A camera cut starts a new temporal coordinate system. Identity temporal
        # transforms are explicit, not a guessed absolute pose/projection.
        if index == 0 and not metadata.reset:
            raise ValueError("First camera frame must explicitly reset temporal history")
        result.append(CameraRecord(pts, camera, metadata))
    return CameraProvider(path, hashlib.sha256(raw).hexdigest(), digest, width, height, str(rate),
                          value["depth_inverted"], value["provenance"], tuple(result))


def load_camera_for_sequence(manifest_path, sequence, *, cancelled=lambda: False):
    if not isinstance(sequence, dict) or sequence.get("schema_version") != 2 or sequence.get("video") is None:
        raise ValueError("Connect the current Video Input Adapter sequence")
    provider = load_camera_manifest(manifest_path, cancelled=cancelled)
    public = sequence.get("public", {})
    if (provider.width, provider.height) != (public.get("width"), public.get("height")):
        raise ValueError("Camera dimensions differ from the active VIDEO view")
    return replace(provider, source_video=sequence["video"])


def validate_projection_depth(camera, inverted):
    """Ensure the declared projection actually maps its near/far to device Z."""
    matrix = camera.view_to_clip
    for z, expected in ((camera.near_plane, int(inverted)), (camera.far_plane, int(not inverted))):
        clip_z, clip_w = z * matrix[10] + matrix[14], z * matrix[11] + matrix[15]
        if abs(clip_w) < 1e-10 or abs(clip_z / clip_w - expected) > 1e-4:
            raise ValueError("Camera projection near/far does not match its device-Z convention")


def validate_camera_depth(camera, depth):
    """Projection consistency is essential; a range-checked depth is not enough."""
    from .external_guides import ExternalGuideProvider
    if not isinstance(camera, CameraProvider) or not isinstance(depth, ExternalGuideProvider):
        raise ValueError("Streamline VIDEO needs explicit camera and numerical depth providers")
    if (depth.role != "depth" or depth.semantics != "device_z" or depth.units != "zero_to_one"
            or depth.channels != 1 or depth.dtype not in ("float16_le", "float32_le")):
        raise ValueError("Streamline requires device_z depth; convert it explicitly with the camera projection")
    if (depth.source_sha256 != camera.source_sha256 or depth.view_id != "source"
            or (depth.source_width, depth.source_height) != (camera.width, camera.height)
            or (depth.width, depth.height) != (camera.width, camera.height)):
        raise ValueError("Camera/depth source hash, view or full-resolution grid differs")
    metadata = json.loads(depth.metadata_json)
    if metadata["reversed_z"] is not camera.depth_inverted:
        raise ValueError("Camera/depth reversed-Z conventions differ")
    for record in camera.frames:
        value = record.camera
        if (not math.isclose(value.near_plane, metadata["near"], rel_tol=1e-5, abs_tol=1e-7)
                or not math.isclose(value.far_plane, metadata["far"], rel_tol=1e-5, abs_tol=1e-7)
                or value.orthographic is not (metadata["projection"] == "orthographic")):
            raise ValueError("Camera/depth near, far or projection type differs")
