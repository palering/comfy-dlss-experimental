"""CPU-only SR/DLAA input planning; this module never loads NGX or fabricates guides.

Enum/parameter mappings follow NVIDIA's nvsdk_ngx_defs.h and
nvsdk_ngx_helpers.h. Dimensions and numeric caps are this experimental adapter's
bounds, not NVIDIA hardware limits. The SDK's optimal-settings query remains
authoritative for supported render/output sizes; quality names are not hardcoded
upscale ratios. Valid metadata is not proof of valid tensors or GPU execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
from .sr_dimensions import dimensions as _dimensions


SR_MODES = {"dlaa": 5, "quality": 2, "balanced": 1,
            "performance": 0, "ultra_performance": 3}
SR_PRESETS = {"default": 0, "j": 10, "k": 11, "l": 12, "m": 13}
SR_EXECUTOR_REASON = "This is an input plan, not rendered output. Execution requires an SDK-enabled CSR1 Worker and runtime/GPU validation."


def _number(value, label, lower, upper, *, inclusive_lower=True):
    if (type(value) not in (int, float) or value > upper
            or (value < lower if inclusive_lower else value <= lower) or not math.isfinite(value)):
        raise ValueError(f"{label} is outside the finite contract range")


@dataclass(frozen=True)
class SRSettings:
    mode: str = "quality"
    preset: str = "default"
    hdr: bool = False
    depth_inverted: bool = False
    motion_jittered: bool = False
    auto_exposure: bool = True
    jitter_policy: str = "unjittered_video"

    def __post_init__(self):
        if not isinstance(self.mode, str) or self.mode not in SR_MODES:
            raise ValueError("Choose DLAA, Quality, Balanced, Performance or Ultra Performance")
        if not isinstance(self.preset, str) or self.preset not in SR_PRESETS:
            raise ValueError("SR preset must be Default, J, K, L or M, not an NR preset")
        for key in ("hdr", "depth_inverted", "motion_jittered", "auto_exposure"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be boolean")
        if self.jitter_policy not in ("unjittered_video", "external_render_metadata"):
            raise ValueError("Jitter must be zero for finished video or supplied by actual render metadata")
        if self.motion_jittered and self.jitter_policy != "external_render_metadata":
            raise ValueError("Unjittered video cannot declare jittered motion vectors")

    def to_payload(self):
        return {"schema_version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("Connect DLSS SR / DLAA Settings")
        allowed = {item.name for item in fields(cls)} | {"schema_version"}
        if set(value) - allowed:
            raise ValueError("Unknown SR settings fields")
        return cls(**{key: item for key, item in value.items() if key != "schema_version"})


@dataclass(frozen=True)
class SRFrameMetadata:
    """Frame values are declarative; callers must bind them to the actual frame."""
    jitter_x: float = 0.0
    jitter_y: float = 0.0
    motion_scale_x: float = 1.0
    motion_scale_y: float = 1.0
    exposure: float = 1.0
    pre_exposure: float = 1.0
    exposure_scale: float = 1.0
    frame_time_ms: float = 1000.0 / 30.0
    reset: bool = False

    def __post_init__(self):
        for key in ("jitter_x", "jitter_y"):
            _number(getattr(self, key), key, -0.5, 0.5)
        for key in ("motion_scale_x", "motion_scale_y"):
            value = getattr(self, key)
            _number(value, key, -16384, 16384)
            if value == 0:
                raise ValueError("Motion scale cannot be zero")
        for key in ("exposure", "pre_exposure", "exposure_scale"):
            _number(getattr(self, key), key, 0, 65504, inclusive_lower=False)
        _number(self.frame_time_ms, "frame_time_ms", 0, 60000, inclusive_lower=False)
        if type(self.reset) is not bool:
            raise ValueError("reset must be boolean")

    def validate_for(self, settings):
        settings = SRSettings.from_payload(settings)
        if settings.jitter_policy == "unjittered_video" and (self.jitter_x != 0 or self.jitter_y != 0):
            raise ValueError("Do not invent subpixel jitter for finished video")


@dataclass(frozen=True)
class SRGuide:
    """Declared plane metadata only; no opaque tensor is accepted as valid here.

Device-depth provenance must be actual render depth or an explicitly calibrated
projection. Relative monocular depth is not interchangeable with device depth.
"""
    role: str
    width: int
    height: int
    format: str
    semantics: str
    provenance: str
    frame_alignment: str = "source_frame"
    depth_inverted: bool | None = None
    color_transfer: str | None = None

    def __post_init__(self):
        _dimensions(self.width, self.height)
        expected = {
            "color": ("rgba16f_le", "declared_color_space", ("source_pixels",)),
            "motion": ("rg16f_le", "current_to_previous_input_pixels_top_left_xy",
                       ("image_estimate", "engine_vectors")),
            "depth": ("r32f_le", "device_depth_0_1",
                      ("render_device_depth", "calibrated_projection_estimate")),
        }
        if not isinstance(self.role, str) or self.role not in expected:
            raise ValueError("SR guide role must be color, motion or depth")
        fmt, semantics, provenance = expected[self.role]
        if self.format != fmt or self.semantics != semantics or self.provenance not in provenance:
            raise ValueError(f"Invalid {self.role} guide format/semantics/provenance; no implicit conversion is allowed")
        if self.frame_alignment != "source_frame":
            raise ValueError("SR planes must align with the same source frame")
        if self.role == "depth":
            if type(self.depth_inverted) is not bool:
                raise ValueError("Device depth must declare forward or inverted encoding")
        elif self.depth_inverted is not None:
            raise ValueError("Only depth may declare depth_inverted")
        if self.role == "color":
            if self.color_transfer not in ("srgb", "linear_sdr", "linear_hdr"):
                raise ValueError("SR color must explicitly declare srgb, linear_sdr or linear_hdr transfer")
        elif self.color_transfer is not None:
            raise ValueError("Only color may declare color_transfer")


def plan_sr(settings, input_width, input_height, output_width=None, output_height=None,
            *, guides=(), frame_metadata=None, external_jitter_available=False):
    """Plan extents and requirements, without decoding or claiming runtime support.

SR requires an explicit target size; DLAA defaults to source size. Settings are
not rewritten to force a quality ratio or to synthesize a geometry/jitter input.
"""
    settings = SRSettings.from_payload(settings)
    _dimensions(input_width, input_height)
    if settings.mode == "dlaa" and output_width is None and output_height is None:
        output_width, output_height = input_width, input_height
    if output_width is None or output_height is None:
        raise ValueError("SR requires explicit output width and height; quality is not an assumed scale factor")
    _dimensions(output_width, output_height, output=True)
    if (output_width < input_width or output_height < input_height
            or input_width * output_height != input_height * output_width):
        raise ValueError("SR must preserve the exact aspect ratio without downscaling")
    equal = (input_width, input_height) == (output_width, output_height)
    if (settings.mode == "dlaa") != equal:
        raise ValueError("DLAA requires equal input/output dimensions; SR requires larger output")
    if type(external_jitter_available) is not bool:
        raise ValueError("external_jitter_available must be boolean")
    if frame_metadata is not None:
        if not isinstance(frame_metadata, SRFrameMetadata):
            raise ValueError("frame_metadata must be validated SRFrameMetadata")
        frame_metadata.validate_for(settings)
    available = {}
    if not isinstance(guides, (tuple, list)):
        raise ValueError("SR guides must be a tuple or list of validated plane metadata")
    for guide in guides:
        if not isinstance(guide, SRGuide) or guide.role in available:
            raise ValueError("SR guides must be validated and have unique roles")
        if (guide.width, guide.height) != (input_width, input_height):
            raise ValueError("SR guide extent differs from the input; explicit resampling is required")
        if guide.role == "depth" and guide.depth_inverted != settings.depth_inverted:
            raise ValueError("Depth encoding does not match the SR depth_inverted setting")
        if guide.role == "color" and (guide.color_transfer == "linear_hdr") != settings.hdr:
            raise ValueError("Color transfer does not match the SR HDR setting; explicit color conversion is required")
        available[guide.role] = guide
    missing = [role for role in ("color", "motion", "depth") if role not in available]
    if settings.jitter_policy == "external_render_metadata" and not external_jitter_available:
        missing.append("actual_jitter_metadata")
    if not settings.auto_exposure and frame_metadata is None:
        missing.append("positive_exposure_metadata")
    warnings = [
        "This report validates metadata only, not plane contents, timestamps, calibration or GPU compatibility.",
        "The NGX optimal-settings query must validate the requested dimensions for the selected runtime; no fixed quality ratio is assumed.",
        "Normals, materials and ray-tracing buffers are not required by this SR input contract; this is not Ray Reconstruction.",
    ]
    if settings.jitter_policy == "unjittered_video":
        warnings.append("Finished video uses zero jitter. It lacks independently jittered render samples; optical flow cannot recreate that information.")
    if available.get("motion") and available["motion"].provenance == "image_estimate":
        warnings.append("Optical flow estimates image motion, not geometry vectors; occlusion, transparency and scene cuts need explicit handling.")
    if available.get("depth") and available["depth"].provenance == "calibrated_projection_estimate":
        warnings.append("Projected estimated depth remains estimated geometry, not an engine ground-truth depth buffer.")
    if settings.hdr:
        warnings.append("The existing SDR video adapter is not an HDR reconstruction path; HDR input/color handling needs separate validation.")
    inputs = input_width * input_height
    outputs = output_width * output_height
    native = {"input_width": input_width, "input_height": input_height,
              "output_width": output_width, "output_height": output_height,
              "mode": SR_MODES[settings.mode], "preset": SR_PRESETS[settings.preset],
              **{key: getattr(settings, key) for key in ("hdr", "depth_inverted", "motion_jittered", "auto_exposure")}}
    return {
        "schema_version": 1, "kind": "sr_input_plan", "state": "blocked", "feature": "dlaa" if equal else "sr",
        "settings": settings.to_payload(), "native_settings": native,
        "input": {"width": input_width, "height": input_height, "pixels": inputs,
                  "inverse_dimensions": [1 / input_width, 1 / input_height]},
        "output": {"width": output_width, "height": output_height, "pixels": outputs,
                   "inverse_dimensions": [1 / output_width, 1 / output_height]},
        "required_guides": {"color": "rgba16f_le / declared color transfer / source frame",
                            "motion": "rg16f_le / current-to-previous input-pixel displacement / top-left XY",
                            "depth": "r32f_le / finite device depth [0,1] / explicit forward-or-inverted encoding"},
        "guides": {role: asdict(guide) for role, guide in available.items()},
        "frame_metadata": asdict(frame_metadata) if frame_metadata is not None else None,
        "jitter": {"policy": settings.jitter_policy, "synthetic_jitter": False,
                   "units": "input_pixels", "external_metadata_declared": external_jitter_available},
        "reset_policy": "first_frame_and_scene_cut_and_sequence_discontinuity",
        "exposure_policy": "ngx_auto_exposure" if settings.auto_exposure else "explicit_positive_frame_metadata",
        "raw_plane_bytes_per_frame": {"color": inputs * 8, "motion": inputs * 4,
                                      "depth": inputs * 4, "output": outputs * 8,
                                      "total": inputs * 16 + outputs * 8},
        "missing_inputs": missing, "metadata_complete": not missing,
        "runnable": False, "blocked_reason": SR_EXECUTOR_REASON,
        "validation": {"plane_contents": "not_checked", "sdk_optimal_settings": "not_queried",
                       "gpu": "not_executed", "executor": "requires_sdk_worker_validation"},
        "warnings": warnings,
    }


def inspect_sr_attachments(report, settings, sequence, attachments):
    """Enrich a plan with connected provider declarations, never read frame files.

Do not translate manifest claims into SRGuide provenance: a manifest does not
prove engine geometry, FP16 conversion, calibration or decoded color contents.
The returned report separates declared compatibility from required preparation.
"""
    from .external_guides import ExternalGuideProvider, validate_sr_guides

    settings = SRSettings.from_payload(settings)
    width, height = report["input"]["width"], report["input"]["height"]
    providers, compatible, attached = {}, set(), []
    pending = ["Decode color, validate transfer/HDR policy and prepare RGBA16F input; not performed by this inspector."]
    input_status = {
        "color": {"state": "conversion_pending" if sequence is not None else "missing",
                  "reason": "Source VIDEO is connected but its decoded color/FP16 plane has not been validated." if sequence is not None
                  else "No source VIDEO or color plane is connected."},
        "motion": {"state": "missing", "reason": "No external numerical motion declaration is connected; configured estimators are not run by this inspector."},
        "depth": {"state": "missing", "reason": "No external numerical device-depth declaration is connected."},
    }
    for attachment in attachments:
        provider = attachment.payload
        if not isinstance(provider, ExternalGuideProvider):
            attached.append({"role": attachment.role, "semantics": attachment.semantics,
                             "declaration": "opaque_attachment", "sr_compatibility": {
                                 "state": "unrecognized", "reasons": ["Attachment is not a validated external numerical provider."]}})
            if attachment.role in input_status:
                input_status[attachment.role] = {"state": "unrecognized", "reason": "Connected opaque attachment does not provide a numerical manifest contract."}
            continue
        entry = provider.report()
        entry["declaration"] = "provided_manifest_not_frame_contents"
        reasons = []
        role = provider.role
        if role != attachment.role or attachment.semantics != provider.semantics:
            reasons.append("Attachment role/semantics do not match its numerical provider.")
        if sequence is None or provider.source_video is not sequence["video"]:
            reasons.append("Guide is not bound to this source VIDEO.")
        if provider.view_id != "source":
            reasons.append("This planner accepts the unmodified source view; cropped/rescaled views need an explicit adapter.")
        if (provider.source_width, provider.source_height) != (width, height) or (provider.width, provider.height) != (width, height):
            reasons.append("Guide source/grid dimensions do not match the input; resampling is not implicit.")
        metadata = entry["metadata"]
        if role == "motion":
            if provider.semantics != "current_to_previous_pixels_top_left_xy" or provider.units != "input_pixels":
                reasons.append("SR needs current-to-previous input-pixel XY motion; UV conversion or flow inversion is not implicit.")
            if metadata.get("includes_camera_motion") is not True:
                reasons.append("Motion must include camera motion.")
            if metadata.get("includes_jitter") is not settings.motion_jittered:
                reasons.append("Motion includes_jitter does not match SR motion_jittered.")
        elif role == "depth":
            if provider.semantics != "device_z" or provider.units != "zero_to_one":
                reasons.append("SR needs device_z depth; relative/linear depth requires explicit calibrated projection conversion.")
            elif metadata.get("reversed_z") is not settings.depth_inverted:
                reasons.append("Depth reversed_z does not match SR depth_inverted.")
        else:
            entry["sr_compatibility"] = {"state": "not_required", "reasons": ["This SR contract does not consume this guide role."] + reasons}
            attached.append(entry)
            continue
        providers[role] = provider
        state = "incompatible" if reasons else "declared_compatible"
        entry["sr_compatibility"] = {"state": state, "reasons": reasons,
                                      "plane_contents": "not_read", "source_content_hash": "not_recomputed"}
        input_status[role] = {"state": state, "reason": "; ".join(reasons) if reasons else
                              "Manifest semantics match; requested frames still need hash, finite/range, timestamp and output-format checks."}
        if not reasons:
            compatible.add(role)
            if role == "motion" and provider.dtype != "float16_le":
                pending.append("Convert declared float32 motion to RG16F with finite/range checks before SR evaluation.")
            elif role == "depth" and provider.dtype != "float32_le":
                pending.append("Convert declared float16 device depth to R32F before SR evaluation.")
        attached.append(entry)
    pair = {"state": "incomplete", "reason": "Both numerical motion and depth declarations are required."}
    if "motion" in providers and "depth" in providers:
        try:
            pair = validate_sr_guides(providers["motion"], providers["depth"])
        except ValueError as error:
            pair = {"state": "incompatible", "reason": str(error)}
            compatible.difference_update(("motion", "depth"))
            for role in ("motion", "depth"):
                input_status[role] = {"state": "incompatible", "reason": str(error)}
        else:
            if compatible != {"motion", "depth"}:
                pair = {**pair, "state": "incompatible", "reason": "Provider declarations conflict with SR settings; inspect attached_guides."}
    result = dict(report)
    deferred = []
    if sequence is not None and "settings" in sequence:
        from .video_pipeline import guide_settings
        configured_motion = guide_settings(sequence["settings"]).motion_provider
        if input_status["motion"]["state"] == "missing" and configured_motion in {"dis", "nvidia", "zero"}:
            input_status["motion"] = {"state": "preparation_pending", "provider": configured_motion,
                                      "reason": "Configured motion is prepared during rendering, not by this inspector; zero mode is diagnostic."}
            deferred.append("motion")
            pending.append("Prepare configured motion for the requested source frames and validate it against device depth.")
            pair = {"state": "deferred", "reason": "Motion/depth timestamp pairing is checked during requested-range execution."}
    result.update({"state": "blocked", "attached_guides": attached,
                   "input_status": input_status, "guide_pair_validation": pair,
                   "pending_preparations": pending,
                   "deferred_inputs": deferred,
                   "missing_inputs": [role for role in report["missing_inputs"] if role not in compatible and role not in deferred],
                   "metadata_complete": False, "runnable": False})
    return result
