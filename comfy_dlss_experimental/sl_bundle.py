"""Content-bound renderer frames for CXR1. No camera/depth/G-buffer estimation.

Version 1 is SDR BT.709 primaries, CFR, top-left planes, row-major camera
matrices and current-to-previous, unjittered input-pixel motion. Pixel payloads
remain on disk; each bounded read checks exact size and SHA-256 before use.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from .sl_contract import CameraFrame, SLFrameMetadata, ReconstructionSettings, RRGuides

MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_FRAMES = 100_000
PLANE_BYTES = {"color": 8, "motion": 4, "depth": 4, "diffuse_albedo": 8,
               "specular_albedo": 8, "normal_roughness": 8, "specular_motion": 4}
BASE_PLANES = frozenset({"color", "motion", "depth"})


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid bundle {name}")
    return value


def _cancel(cancelled):
    if cancelled():
        raise InterruptedError("Reconstruction input cancelled")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate bundle JSON field: " + key)
        result[key] = value
    return result


def _path(root, raw):
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw or ":" in raw:
        raise ValueError("Bundle files require relative POSIX paths")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Bundle file escapes its directory")
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Bundle file escapes its directory or is not a regular file")
    return path


@dataclass(frozen=True)
class BundleFile:
    path: str
    sha256: str

    @classmethod
    def parse(cls, value, root):
        if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
            raise ValueError("Each bundle file needs exactly path and sha256")
        digest = value["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid bundle file SHA-256")
        _path(root, value["path"])
        return cls(**value)

    def read(self, root, size, cancelled):
        _cancel(cancelled)
        path = _path(root, self.path)
        with path.open("rb") as file:
            if path.stat().st_size != size:
                raise ValueError("Bundle plane has the wrong byte length: " + self.path)
            data = file.read(size + 1)
        _cancel(cancelled)
        if len(data) != size or hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError("Bundle plane changed or SHA-256 mismatch: " + self.path)
        return data


@dataclass(frozen=True)
class BundleFrame:
    pts_ns: int
    camera: CameraFrame
    metadata: SLFrameMetadata
    planes: tuple[tuple[str, BundleFile], ...]
    world_to_view: tuple = ()
    view_to_world: tuple = ()


@dataclass(frozen=True)
class ReconstructionBundle:
    manifest_path: Path
    manifest_sha256: str
    width: int
    height: int
    fps: str
    color_transfer: str
    jitter_policy: str
    depth_inverted: bool
    frames: tuple[BundleFrame, ...]
    has_rr: bool
    audio_source: BundleFile | None

    def report(self):
        return {"schema_version": 1, "manifest_path": str(self.manifest_path),
                "manifest_sha256": self.manifest_sha256, "width": self.width, "height": self.height,
                "fps": self.fps, "frame_count": len(self.frames), "has_rr": self.has_rr,
                "color_transfer": self.color_transfer, "jitter_policy": self.jitter_policy,
                "depth_inverted": self.depth_inverted, "has_audio_source": self.audio_source is not None,
                "validation": "manifest_metadata_and_file_sizes_only; hashes_checked_on_read",
                "camera_inferred": False, "missing_guides_fabricated": False}

    def settings(self, feature="sr", mode="quality", output_width=960, output_height=540):
        if feature == "rr" and not self.has_rr:
            raise ValueError("RR requires real albedos, normal/roughness, reflection motion and world/view matrices")
        if mode == "dlaa":
            output_width, output_height = self.width, self.height
        result = ReconstructionSettings(self.width, self.height, output_width, output_height,
            feature=feature, mode=mode, color_transfer=self.color_transfer,
            jitter_policy=self.jitter_policy, depth_inverted=self.depth_inverted)
        if output_width % 2 or output_height % 2:
            raise ValueError("Reconstruction VIDEO output dimensions must be even for YUV420 encoding")
        for frame in self.frames:
            frame.metadata.validate_for(result)
        return result

    def reopen(self, cancelled=lambda: False):
        current = load_bundle(self.manifest_path, cancelled=cancelled)
        if current.manifest_sha256 != self.manifest_sha256:
            raise ValueError("Reconstruction bundle changed after input assembly; rerun the input node")
        return current

    def read_frame(self, index, settings, cancelled=lambda: False):
        if not isinstance(settings, ReconstructionSettings) or (settings.input_width, settings.input_height) != (self.width, self.height):
            raise ValueError("Reconstruction settings differ from the bundle extent")
        if (settings.color_transfer, settings.jitter_policy, settings.depth_inverted) != (
                self.color_transfer, self.jitter_policy, self.depth_inverted):
            raise ValueError("Reconstruction settings differ from bundle declarations")
        _integer(index, "frame index", 0, len(self.frames) - 1)
        frame = self.frames[index]
        frame.metadata.validate_for(settings)
        roles = set(PLANE_BYTES) if settings.feature == "rr" else BASE_PLANES
        if not roles <= dict(frame.planes).keys():
            raise ValueError("Missing RR numerical buffers")
        data = {role: file.read(self.manifest_path.parent, self.width * self.height * PLANE_BYTES[role], cancelled)
                for role, file in frame.planes if role in roles}
        rr = (RRGuides(**{role: data[role] for role in roles - BASE_PLANES},
                       world_to_view=frame.world_to_view, view_to_world=frame.view_to_world)
              if settings.feature == "rr" else None)
        return data["color"], data["motion"], data["depth"], frame, rr


def load_bundle(path, *, cancelled=lambda: False):
    _cancel(cancelled)
    path = Path(path).expanduser().resolve(strict=True)
    with path.open("rb") as file:
        raw = file.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Reconstruction manifest exceeds the 32 MiB metadata limit")
    value = json.loads(raw, object_pairs_hook=_object)
    required = {"schema_version", "kind", "width", "height", "fps", "color_transfer",
                "jitter_policy", "depth_inverted", "has_rr", "frames", "audio_source"}
    if not isinstance(value, dict) or set(value) != required or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "dlss_reconstruction_bundle":
        raise ValueError("Expected an explicit version-1 dlss_reconstruction_bundle manifest")
    from .sr_dimensions import dimensions, LIMITS
    width = _integer(value["width"], "width", LIMITS['min_side'], LIMITS['input_max_side'])
    height = _integer(value["height"], "height", LIMITS['min_side'], LIMITS['input_max_side'])
    dimensions(width,height)
    for name in ("depth_inverted", "has_rr"):
        if type(value[name]) is not bool:
            raise ValueError("Bundle " + name + " must be boolean")
    if value["color_transfer"] not in ("srgb", "linear_sdr") or value["jitter_policy"] not in ("unjittered_video", "external_render_metadata"):
        raise ValueError("Bundle requires declared SDR transfer and actual jitter policy")
    if value["has_rr"] and value["color_transfer"] != "linear_sdr":
        raise ValueError("RR buffers require linear_sdr color")
    if not isinstance(value["fps"], str) or len(value["fps"]) > 32:
        raise ValueError("Bundle fps must be an explicit rational string")
    try:
        rate = Fraction(value["fps"])
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("Invalid bundle frame rate") from error
    if not 1 <= rate <= 120:
        raise ValueError("Bundle CFR frame rate must be in [1,120]")
    items = value["frames"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BUNDLE_FRAMES:
        raise ValueError("Bundle must contain 1..100000 frames within the metadata size limit")
    roles = set(PLANE_BYTES) if value["has_rr"] else BASE_PLANES
    frame_keys = {"pts_ns", "camera", "metadata", "planes"} | ({"world_to_view", "view_to_world"} if value["has_rr"] else set())
    metadata_keys = {field.name for field in fields(SLFrameMetadata)}
    frames = []
    for index, item in enumerate(items):
        _cancel(cancelled)
        if not isinstance(item, dict) or set(item) != frame_keys:
            raise ValueError("Bundle frame requires complete camera, metadata and numerical planes")
        pts = _integer(item["pts_ns"], "PTS", 0, (1 << 63) - 1)
        if abs(pts - round(index * 1_000_000_000 / rate)) > 1:
            raise ValueError("Bundle timestamps must be zero-based CFR; no implicit retiming")
        camera = CameraFrame.from_payload(item["camera"])
        camera.validate_extent(width, height)
        if not isinstance(item["metadata"], dict) or set(item["metadata"]) != metadata_keys:
            raise ValueError("Bundle frames require all explicit jitter/exposure/reset metadata")
        metadata = SLFrameMetadata(**item["metadata"])
        if value["jitter_policy"] == "unjittered_video" and (metadata.jitter_x or metadata.jitter_y):
            raise ValueError("Unjittered bundle cannot contain invented jitter")
        if not isinstance(item["planes"], dict) or set(item["planes"]) != roles:
            raise ValueError("Bundle plane roles do not match has_rr")
        planes = tuple((role, BundleFile.parse(item["planes"][role], path.parent)) for role in sorted(roles))
        for role, plane in planes:
            if _path(path.parent, plane.path).stat().st_size != width * height * PLANE_BYTES[role]:
                raise ValueError("Bundle plane has the wrong byte length: " + plane.path)
        world, view = (), ()
        if value["has_rr"]:
            matrices = RRGuides(b"", b"", b"", b"", item["world_to_view"], item["view_to_world"])
            world, view = matrices.world_to_view, matrices.view_to_world
        frames.append(BundleFrame(pts, camera, metadata, planes, world, view))
    audio = None if value["audio_source"] is None else BundleFile.parse(value["audio_source"], path.parent)
    return ReconstructionBundle(path, hashlib.sha256(raw).hexdigest(), width, height, str(rate),
        value["color_transfer"], value["jitter_policy"], value["depth_inverted"], tuple(frames), value["has_rr"], audio)
