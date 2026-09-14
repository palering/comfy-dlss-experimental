"""Lazy, content-addressed numerical guides supplied by external estimators.

This boundary neither runs a model nor treats an RGB visualization as geometry.
Manifests bind a specific source/view and exact nanosecond timestamps. Only the
requested frame is read, hash checked and validated. No backend consumption is
implied by successfully loading a provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from bisect import bisect_left, bisect_right
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import struct

MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_FRAME_BYTES = 256 * 1024 * 1024
MAX_FRAMES = 100_000
_DTYPES = {"float16_le": (2, "<e"), "float32_le": (4, "<f")}
_ROLES = {
    "motion": (2, {"current_to_previous_pixels_top_left_xy", "previous_to_current_pixels_top_left_xy",
                   "current_to_previous_uv_top_left_xy"}),
    "depth": (1, {"linear_view_z", "inverse_view_z", "device_z"}),
    "normals": (3, {"view_space_xyz", "world_space_xyz"}),
    "confidence": (1, {"motion_confidence", "depth_confidence"}),
    "mask": (1, {"validity", "reactive", "transparency_composition"}),
}


def _int(value, name, low=1, high=(1 << 63) - 1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _sha(value, name):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA256")
    return value


def _keys(value, allowed, name, required=None):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required or allowed) <= set(value):
        raise ValueError(f"Invalid {name} fields")
    return value


def _relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Guide frame path must be relative to the manifest directory")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in ("..", ".") for part in value.split("/")):
        raise ValueError("Guide frame path cannot escape the manifest directory")
    return value


@dataclass(frozen=True)
class GuideFrame:
    data: bytes = field(repr=False)
    width: int
    height: int
    channels: int
    dtype: str
    role: str
    semantics: str
    pts_ns: int

    def as_numpy(self):
        """Optional, zero-copy read-only numerical view; NumPy is imported lazily."""
        import numpy as np
        dtype = "<f2" if self.dtype == "float16_le" else "<f4"
        return np.frombuffer(self.data, dtype=dtype).reshape(self.height, self.width, self.channels)


@dataclass(frozen=True)
class GuideFile:
    path: str
    sha256: str
    pts_ns: int
    reset: bool = False


@dataclass(frozen=True)
class ExternalGuideProvider:
    manifest_path: Path
    source_video: object = field(repr=False, compare=False)
    source_sha256: str
    view_id: str
    source_width: int
    source_height: int
    role: str
    semantics: str
    units: str
    width: int
    height: int
    channels: int
    dtype: str
    storage: str
    files: tuple[GuideFile, ...] = field(repr=False)
    metadata_json: str = field(repr=False)
    cache_identity: str
    _timestamps: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_timestamps", tuple(entry.pts_ns for entry in self.files))

    def __deepcopy__(self, memo):
        # All provider fields are immutable; preserve opaque VIDEO identity.
        return self

    @property
    def frame_count(self):
        return len(self.files)

    def report(self):
        return {
            "schema_version": 1, "kind": "external_numerical_guide", "role": self.role,
            "semantics": self.semantics, "units": self.units,
            "source_sha256": self.source_sha256, "view_id": self.view_id,
            "source_size": [self.source_width, self.source_height],
            "source_info": {"sha256": self.source_sha256, "view_id": self.view_id,
                            "width": self.source_width, "height": self.source_height,
                            "time_origin": "video_stream_start"},
            "grid_size": [self.width, self.height], "grid_sampling": "pixel_centers",
            "dtype": self.dtype, "storage": self.storage, "frames": self.frame_count,
            "metadata": json.loads(self.metadata_json), "cache_identity": self.cache_identity,
            "manifest_contract_sha256": self.cache_identity,
            "validation": "manifest_only; requested frames are hash/shape/finite checked on read",
            "backend_consumption": "must_be_explicitly_negotiated",
        }

    def snapshot_config(self):
        """Small serializable cache input; never serializes frame contents/VIDEO."""
        return {"manifest_path": str(self.manifest_path), "cache_identity": self.cache_identity}

    def read_at(self, pts_ns, *, source_sha256):
        """NR adapter: exact source extent/backward flow, at most 1 us rounding.

The tolerance covers time-base -> nanosecond rounding only, not a resampling
policy. Two candidates inside that tolerance are rejected as ambiguous.
"""
        _int(pts_ns, "Requested pts_ns", 0)
        self.validate_nr_motion()
        times = self._timestamps
        index = self.index_at(pts_ns)
        frame = self.read_frame(index, source_sha256=source_sha256, view_id="source", pts_ns=times[index])
        values = frame.as_numpy()
        if (abs(values) > 65504).any():
            raise ValueError("External motion exceeds finite FP16 NR transport range")
        return values, {"reset": index == 0 or self.files[index].reset,
                        "frame_index": index, "pts_ns": times[index]}

    def index_at(self, pts_ns):
        _int(pts_ns, "Requested pts_ns", 0)
        first = bisect_left(self._timestamps, pts_ns - 1000)
        after = bisect_right(self._timestamps, pts_ns + 1000)
        if after - first != 1:
            raise ValueError("External guide has no unique matching timestamp; rebuild/resample explicitly")
        return first

    def validate_nr_motion(self):
        if self.role != "motion" or self.semantics != "current_to_previous_pixels_top_left_xy":
            raise ValueError("NR external motion requires current-to-previous pixel XY flow; no implicit inversion")
        if self.view_id != "source" or (self.width, self.height) != (self.source_width, self.source_height):
            raise ValueError("NR external motion requires the unmodified source view and full source grid")
        metadata = json.loads(self.metadata_json)
        if not metadata["includes_camera_motion"] or metadata["includes_jitter"]:
            raise ValueError("NR external motion must include camera motion and exclude projection jitter")

    def to_attachment(self):
        from .media_pipeline import GuideAttachment
        return GuideAttachment(self.role, self.source_video, self.semantics, self)

    def read_frame(self, index, *, source_sha256, view_id, pts_ns):
        """Read one exact source frame. No resampling, time matching or fallback."""
        _int(index, "Guide frame index", 0, self.frame_count - 1)
        _int(pts_ns, "Requested pts_ns", 0)
        entry = self.files[index]
        if source_sha256 != self.source_sha256 or view_id != self.view_id:
            raise ValueError("Guide source content/view identity mismatch")
        if pts_ns != entry.pts_ns:
            raise ValueError("Guide timestamp mismatch; resample/rebuild guides explicitly")
        root = self.manifest_path.parent.resolve()
        path = (root / entry.path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Guide frame resolves outside the manifest directory")
        expected = self.width * self.height * self.channels * _DTYPES[self.dtype][0]
        # npy headers are bounded; compressed .npz/object archives are not allowed.
        limit = expected + (65536 if self.storage == "npy" else 0)
        with path.open("rb") as handle:
            content = handle.read(limit + 1)
        if len(content) > limit:
            raise ValueError("Guide frame exceeds its declared size")
        if hashlib.sha256(content).hexdigest() != entry.sha256:
            raise ValueError("Guide frame SHA256 mismatch; rebuild the manifest/cache identity")
        if self.storage == "raw":
            data = content
        else:
            data = self._read_npy(content)
        if len(data) != expected:
            raise ValueError("Guide frame has a different byte length/shape than declared")
        self._validate_values(data)
        return GuideFrame(data, self.width, self.height, self.channels, self.dtype,
                          self.role, self.semantics, pts_ns)

    def _read_npy(self, content):
        import numpy as np
        # Reject hostile shape declarations before np.load can allocate anything.
        stream = io.BytesIO(content)
        version = np.lib.format.read_magic(stream)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream, max_header_size=65536)
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream, max_header_size=65536)
        else:
            raise ValueError("Only numeric .npy format 1.0/2.0 is supported")
        valid_shapes = {(self.height, self.width, self.channels)}
        if self.channels == 1:
            valid_shapes.add((self.height, self.width))
        expected_dtype = np.dtype("<f2" if self.dtype == "float16_le" else "<f4")
        if shape not in valid_shapes or fortran or dtype != expected_dtype or dtype.hasobject:
            raise ValueError("Guide .npy must match declared shape/little-endian floating dtype and C order")
        expected = self.width * self.height * self.channels * expected_dtype.itemsize
        if len(content) - stream.tell() != expected:
            raise ValueError("Guide .npy has truncated or trailing data")
        array = np.load(io.BytesIO(content), allow_pickle=False, max_header_size=65536)
        return array.tobytes(order="C")

    def _validate_values(self, data):
        normalized = self.role in ("confidence", "mask") or self.semantics == "device_z"
        positive = self.role == "depth" and self.semantics in ("linear_view_z", "inverse_view_z")
        try:
            import numpy as np
        except ModuleNotFoundError as error:
            if error.name != "numpy":
                raise
            np = None
        if np is not None:
            # Actual video execution already needs NumPy. Keep per-pixel checks
            # vectorized there, while raw contract tests need no media install.
            values = np.frombuffer(data, dtype="<f2" if self.dtype == "float16_le" else "<f4")
            if not np.isfinite(values).all():
                raise ValueError("Guide values must be finite; use a separate validity mask")
            if normalized and ((values < 0).any() or (values > 1).any()):
                raise ValueError("Normalized guide values must be in [0, 1]")
            if positive and (values <= 0).any():
                raise ValueError("Linear/inverse view depth must be strictly positive")
            if self.role == "normals" and ((values < -1).any() or (values > 1).any()):
                raise ValueError("Normal components must be in [-1, 1]")
            return
        for (value,) in struct.iter_unpack(_DTYPES[self.dtype][1], data):
            if not math.isfinite(value):
                raise ValueError("Guide values must be finite; use a separate validity mask")
            if normalized and not 0 <= value <= 1:
                raise ValueError("Normalized guide values must be in [0, 1]")
            if positive and value <= 0:
                raise ValueError("Linear/inverse view depth must be strictly positive")
            if self.role == "normals" and not -1 <= value <= 1:
                raise ValueError("Normal components must be in [-1, 1]")


def load_external_guide(manifest_path, *, source_video, source_width=None, source_height=None):
    """Graph adapter: bind a numerical manifest to this exact host VIDEO object."""
    if source_video is None:
        raise ValueError("An external guide must be bound to an active VIDEO")
    return replace(load_guide_manifest(manifest_path, source_width=source_width, source_height=source_height),
                   source_video=source_video)


def load_guide_manifest(manifest_path, *, source_width=None, source_height=None):
    """Host-neutral manifest reader; content/view/PTS are the source identity.

    Does not read frame planes, load NumPy, or require any VIDEO object. A graph
    adapter adds host object ownership separately with load_external_guide.
    """
    if not isinstance(manifest_path, (str, Path)) or not str(manifest_path):
        raise ValueError("External guide manifest path is missing")
    path = Path(manifest_path).expanduser().resolve()
    with path.open("rb") as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("External guide manifest exceeds 8 MiB")
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate guide manifest key: {key}")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=no_duplicates)
    _keys(value, {"schema_version", "source", "role", "semantics", "units", "grid", "dtype", "storage",
                  "frames", "metadata"}, "guide manifest")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported external guide schema")
    source = _keys(value["source"], {"sha256", "view_id", "width", "height", "time_origin"}, "guide source")
    if source["time_origin"] != "video_stream_start":
        raise ValueError("Guide PTS must be relative to video stream.start_time (time_origin=video_stream_start)")
    source_sha = _sha(source["sha256"], "Source hash")
    view_id = source["view_id"]
    if not isinstance(view_id, str) or not 1 <= len(view_id) <= 256 or any(ord(c) < 32 for c in view_id):
        raise ValueError("Source view_id must explicitly identify the crop/resize/active view")
    sw, sh = _int(source["width"], "Source width", high=16384), _int(source["height"], "Source height", high=16384)
    if source_width is not None and (type(source_width) is not int or source_width != sw):
        raise ValueError("Guide width does not match the active VIDEO view")
    if source_height is not None and (type(source_height) is not int or source_height != sh):
        raise ValueError("Guide height does not match the active VIDEO view")
    role = value["role"]
    if (not isinstance(role, str) or role not in _ROLES or not isinstance(value["semantics"], str)
            or value["semantics"] not in _ROLES[role][1]):
        raise ValueError("Unsupported guide role/semantics; RGB visualizations are not numerical guides")
    grid = _keys(value["grid"], {"width", "height", "sampling"}, "guide grid")
    width, height = _int(grid["width"], "Grid width", high=sw), _int(grid["height"], "Grid height", high=sh)
    if grid["sampling"] != "pixel_centers":
        raise ValueError("Guide grid sampling must explicitly be pixel_centers")
    channels = _ROLES[role][0]
    if not isinstance(value["dtype"], str) or value["dtype"] not in _DTYPES or value["storage"] not in ("raw", "npy"):
        raise ValueError("Guides require raw or .npy little-endian float16/float32 numerical storage")
    if width * height * channels * _DTYPES[value["dtype"]][0] > MAX_FRAME_BYTES:
        raise ValueError("A guide frame exceeds the 256 MiB bounded read limit")
    metadata = _validate_semantics(role, value["semantics"], value["units"], value["metadata"])
    entries = value["frames"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FRAMES:
        raise ValueError("Guide manifest requires 1..100000 per-frame entries")
    files, previous = [], -1
    for item in entries:
        item = _keys(item, {"path", "sha256", "pts_ns", "reset"}, "guide frame", {"path", "sha256", "pts_ns"})
        pts = _int(item["pts_ns"], "Frame pts_ns", 0)
        if pts <= previous:
            raise ValueError("Guide timestamps must be strictly increasing source PTS in nanoseconds")
        previous = pts
        if type(item.get("reset", False)) is not bool:
            raise ValueError("Guide frame reset flag must be boolean")
        files.append(GuideFile(_relative_path(item["path"]), _sha(item["sha256"], "Frame hash"), pts,
                               item.get("reset", False)))
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return ExternalGuideProvider(path, None, source_sha, view_id, sw, sh, role, value["semantics"],
                                 value["units"], width, height, channels, value["dtype"], value["storage"],
                                 tuple(files), json.dumps(metadata, sort_keys=True), hashlib.sha256(canonical).hexdigest())


def _validate_semantics(role, semantics, units, metadata):
    if not isinstance(units, str) or not isinstance(metadata, dict):
        raise ValueError("Guide metadata must be an object")
    if role == "motion":
        expected_units = "normalized_uv" if "_uv_" in semantics else "input_pixels"
        if units != expected_units:
            raise ValueError("Motion units must match the declared direction/coordinate semantics")
        _keys(metadata, {"includes_camera_motion", "includes_jitter"}, "motion metadata")
        if any(type(value) is not bool for value in metadata.values()):
            raise ValueError("Motion metadata flags must be boolean")
    elif role == "depth" and semantics == "device_z":
        _keys(metadata, {"reversed_z", "near", "far", "projection"}, "device depth metadata")
        near, far = metadata["near"], metadata["far"]
        if (units != "zero_to_one" or type(metadata["reversed_z"]) is not bool
                or metadata["projection"] not in ("perspective", "orthographic")
                or type(near) not in (int, float) or type(far) not in (int, float)
                or not math.isfinite(near) or not math.isfinite(far) or not 0 < near < far):
            raise ValueError("Device depth requires zero_to_one, explicit reversed_z, finite near/far and projection")
    elif role == "depth":
        expected = {"meters", "relative"} if semantics == "linear_view_z" else {"inverse_meters", "relative"}
        if units not in expected or metadata:
            raise ValueError("View depth must distinguish metric/relative units; no automatic device-depth conversion")
    elif metadata or units != ("unit_vector" if role == "normals" else "zero_to_one"):
        raise ValueError("Invalid guide units or unexpected metadata")
    return metadata


def select_external_guide(selection, a=None, b=None, c=None):
    """Selected-only lazy branch; deliberately never opens any provider file."""
    if selection not in ("a", "b", "c"):
        raise ValueError("Choose guide input a, b or c")
    selected = {"a": a, "b": b, "c": c}[selection]
    if not isinstance(selected, ExternalGuideProvider):
        raise ValueError(f"Selected guide input {selection} is missing or invalid")
    return selected


def reopen_external_guide(config):
    """Reopen a selected config and reject manifest edits after graph assembly."""
    _keys(config, {"manifest_path", "cache_identity"}, "external guide snapshot")
    _sha(config["cache_identity"], "Guide cache identity")
    if not isinstance(config["manifest_path"], str) or not config["manifest_path"]:
        raise ValueError("External guide manifest path is missing")
    provider = load_guide_manifest(config["manifest_path"])
    if provider.cache_identity != config["cache_identity"]:
        raise ValueError("External guide manifest changed; rerun its provider node before rendering")
    return provider


def validate_sr_guides(motion, depth):
    """Conservative SR preflight, not a claim that a vendor backend is available.

Relative monocular depth cannot stand in for device depth without an explicit
projection conversion. This validator performs no such invented conversion.
"""
    if not isinstance(motion, ExternalGuideProvider) or not isinstance(depth, ExternalGuideProvider):
        raise ValueError("SR requires explicit numerical motion and depth providers")
    if motion.role != "motion" or motion.semantics != "current_to_previous_pixels_top_left_xy":
        raise ValueError("SR requires current-to-previous input-pixel XY motion")
    if depth.role != "depth" or depth.semantics != "device_z":
        raise ValueError("SR requires device_z depth; relative/linear depth needs explicit projection conversion")
    if (motion.source_video is not depth.source_video or motion.source_sha256 != depth.source_sha256
            or motion.view_id != depth.view_id or (motion.source_width, motion.source_height) != (depth.source_width, depth.source_height)
            or tuple(item.pts_ns for item in motion.files) != tuple(item.pts_ns for item in depth.files)):
        raise ValueError("SR guide sources/views/timestamps do not match")
    for provider in (motion, depth):
        if (provider.width, provider.height) != (provider.source_width, provider.source_height):
            raise ValueError("SR guide grids must match input extent; resample explicitly")
    return {"state": "guide_contract_validated", "gpu": "not_probed", "jitter": "must_be_supplied_by_sr_stage",
            "motion_cache_identity": motion.cache_identity, "depth_cache_identity": depth.cache_identity,
            "depth_metadata": json.loads(depth.metadata_json)}
