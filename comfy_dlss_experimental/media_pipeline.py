"""Lazy media/guide assembly and bounded NR stages; no decoding or native calls.

VIDEO is an opaque live Comfy object, never deep-copied or serialized. Metadata
and settings are detached on each graph branch. Source inspection remains a
header snapshot: actual content hashes, PTS and color validation are the existing
range executor's responsibility, not an invented guarantee of reconstruction.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import math

from .backend_contracts import EXTERNAL_D5V2_NR, OWNED_CSR1_SR, OWNED_CXR1_SL
from .feature_requirements import requirements_report
from .flow_provider import FlowProvider
from .input_policy import InputColorPolicy
from .nr_effect import MAX_NR_PASSES, build_pass_stack
from .processing_plan import FeatureStage, compile_legacy_nr
from .video_pipeline import guide_settings, profile_settings


def select_flow(selection: str, a=None, b=None, c=None) -> dict:
    """Validate only the selected configuration; never invoke a provider."""
    if selection not in ("a", "b", "c"):
        raise ValueError("Choose optical-flow input a, b or c")
    selected = {"a": a, "b": b, "c": c}[selection]
    if selected is None:
        raise ValueError(f"Selected optical-flow input {selection} is not connected")
    config = FlowProvider.from_payload(selected)
    return {"schema_version": 1, **asdict(config)}


def _sequence_copy(value) -> dict:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 2:
        raise ValueError("Input assembly requires the current Video Input Adapter sequence")
    if value.get("video") is None:
        raise ValueError("Input sequence has no VIDEO")
    for key in ("settings", "public", "input_report"):
        if key in value and not isinstance(value[key], dict):
            raise ValueError(f"Input sequence {key} must be an object")
    # Preserve opaque VIDEO identity; copying tensors/streams can exhaust memory.
    result = {key: deepcopy(item) for key, item in value.items() if key != "video"}
    result["video"] = value["video"]
    result.setdefault("settings", {})
    guide_settings(result.get("settings", {}))
    InputColorPolicy.from_dict(result.get("color_policy")).validate()
    public = result.get("public", {})
    for key in ("width", "height"):
        size = public.get(key)
        if size is not None and (type(size) is not int or size <= 0):
            raise ValueError(f"Invalid input {key}")
    duration = public.get("duration")
    if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0):
        raise ValueError("Invalid input duration")
    return result


@dataclass(frozen=True)
class GuideAttachment:
    """Future external-data boundary; no current D5V2 consumption is implied.

The originating VIDEO reference identifies the active view, not just a filename.
No arbitrary IMAGE tensor is assumed to be depth or numerical flow. This internal
contract is not yet a user-facing provider or a source of inferred geometry.
"""
    role: str
    source_video: object = field(repr=False)
    semantics: str
    payload: object = field(repr=False)


@dataclass(frozen=True)
class MediaPipeline:
    _sequence: dict = field(repr=False)
    stages: tuple[FeatureStage, ...] = ()
    inherited: tuple[bool, ...] = ()
    attachments: tuple[GuideAttachment, ...] = ()

    def __post_init__(self):
        sequence = _sequence_copy(self._sequence)
        stages = tuple(self.stages)
        inherited = tuple(self.inherited)
        attachments = tuple(self.attachments)
        if len(stages) > MAX_NR_PASSES or len(inherited) != len(stages) or any(type(x) is not bool for x in inherited):
            raise ValueError("Invalid pipeline stage count or inheritance metadata")
        for stage in stages:
            if not isinstance(stage, FeatureStage):
                raise ValueError("Pipeline stages must carry a validated feature contract")
            if stage.backend_id == EXTERNAL_D5V2_NR.backend_id and stage.feature == "nr":
                profile_settings(stage.parameters_copy(), 64, 64, 1, 120)
            elif stage.backend_id == OWNED_CSR1_SR.backend_id and stage.feature in {"sr", "dlaa"}:
                if len(stages) != 1:
                    raise ValueError("SR currently requires a single feature stage; NR/SR composition is not implemented")
                from .sr_contract import plan_sr
                params = stage.parameters_copy()
                planned = plan_sr(params["settings"], sequence["public"].get("width"), sequence["public"].get("height"),
                                  params["output_width"], params["output_height"])
                if planned["feature"] != stage.feature:
                    raise ValueError("SR stage feature and target dimensions disagree")
            elif stage.backend_id == OWNED_CXR1_SL.backend_id and stage.feature in {"sr", "dlaa"}:
                if len(stages) != 1:
                    raise ValueError("Streamline currently requires one stage; mixed feature composition is not implemented")
                from .sl_contract import ReconstructionSettings
                params = stage.parameters_copy()
                if set(params) != {"mode", "output_width", "output_height"}:
                    raise ValueError("Invalid Streamline stage parameters")
                native = ReconstructionSettings(sequence["public"]["width"], sequence["public"]["height"],
                                               **params)
                if stage.feature != ("dlaa" if native.mode == "dlaa" else "sr"):
                    raise ValueError("Streamline mode and stage feature disagree")
            else:
                raise ValueError("Unsupported pipeline feature/backend pairing")
        roles = set()
        for item in attachments:
            if not isinstance(item, GuideAttachment) or item.role not in ("depth", "normals", "mask", "confidence", "motion", "camera"):
                raise ValueError("Unsupported guide attachment")
            if item.role == "motion":
                from .external_guides import ExternalGuideProvider
                if not isinstance(item.payload, ExternalGuideProvider):
                    raise ValueError("Motion attachments require an explicit numerical provider")
            if item.role == "camera":
                from .camera_provider import CameraProvider
                if (not isinstance(item.payload, CameraProvider) or item.payload.source_video is not sequence["video"]
                        or item.semantics != item.payload.semantics):
                    raise ValueError("Camera attachments require an explicit source-bound camera provider")
            if item.role in roles or item.source_video is not sequence["video"] or not item.semantics:
                raise ValueError("Guide source/active view mismatch or ambiguous guide semantics")
            roles.add(item.role)
        object.__setattr__(self, "_sequence", sequence)
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "inherited", inherited)
        object.__setattr__(self, "attachments", attachments)

    def sequence_copy(self) -> dict:
        return _sequence_copy(self._sequence)


def require_pipeline(value) -> MediaPipeline:
    if not isinstance(value, MediaPipeline):
        raise ValueError("Connect a DLSS Input Assembler, NR Stage or SR Stage")
    return value


def assemble_input(sequence, flow_provider=None, settings=None, attachments=(), *, external_motion=None,
                   motion_source="configured") -> MediaPipeline:
    source = _sequence_copy(sequence)
    if settings is not None:
        if not isinstance(settings, dict):
            raise ValueError("Guide settings must be an object")
        source["settings"] = deepcopy(settings)
    if motion_source not in ("configured", "external"):
        raise ValueError("Choose configured or external motion source")
    if motion_source == "external":
        from .external_guides import ExternalGuideProvider
        if not isinstance(external_motion, ExternalGuideProvider) or external_motion.role != "motion":
            raise ValueError("External motion mode requires a numerical motion provider")
        if external_motion.source_video is not source["video"]:
            raise ValueError("Guide source/active view mismatch")
        external_motion.validate_nr_motion()
        source["settings"].pop("flow_provider", None)
        source["settings"]["motion_provider"] = "external"
        source["settings"]["external_motion"] = external_motion.snapshot_config()
        attachments = (*attachments, external_motion.to_attachment())
    elif flow_provider is not None:
        source["settings"].pop("external_motion", None)
        provider = FlowProvider.from_payload(flow_provider)
        source["settings"]["flow_provider"] = {"schema_version": 1, **asdict(provider)}
    parsed = guide_settings(source["settings"])
    source["settings"]["motion_provider"] = parsed.motion_provider
    source.setdefault("public", {})["settings"] = deepcopy(source["settings"])
    if source.get("input_report"):
        source["input_report"]["guide_mode"] = parsed.motion_provider
    return MediaPipeline(source, attachments=tuple(attachments))


def append_nr(pipeline, profile, enabled=True) -> MediaPipeline:
    pipeline = require_pipeline(pipeline)
    if any(stage.feature != "nr" for stage in pipeline.stages):
        raise ValueError("NR/SR composition is not implemented; use separate source branches")
    if type(enabled) is not bool:
        raise ValueError("Stage enabled must be boolean")
    plan = compile_legacy_nr(profile)
    profiles, metadata = plan.legacy_parts()
    if len(pipeline.stages) + len(profiles) > MAX_NR_PASSES:
        raise ValueError("Current NR pipeline supports at most three total passes, including connected Stacks")
    stages = []
    for leaf in profiles:
        if not enabled:
            leaf["nr_enabled"] = False
        stages.append(FeatureStage(EXTERNAL_D5V2_NR.backend_id, "nr", leaf))
    return MediaPipeline(pipeline.sequence_copy(), pipeline.stages + tuple(stages),
                         pipeline.inherited + tuple(metadata["inherited"]), pipeline.attachments)


def append_sr(pipeline, settings, output_width, output_height) -> MediaPipeline:
    """Add one explicit SR/DLAA stage; do not silently discard earlier NR work."""
    from .sr_contract import SRSettings, plan_sr
    pipeline = require_pipeline(pipeline)
    if pipeline.stages:
        raise ValueError("Connect Input Assembler directly: mixed NR/SR or multiple SR stages are not implemented")
    settings = SRSettings.from_payload(settings)
    source = pipeline.sequence_copy()
    public = source["public"]
    if settings.mode == "dlaa":
        output_width, output_height = public["width"], public["height"]
    planned = plan_sr(settings, public.get("width"), public.get("height"), output_width, output_height)
    stage = FeatureStage(OWNED_CSR1_SR.backend_id, planned["feature"], {
        "settings": settings.to_payload(), "output_width": output_width, "output_height": output_height})
    return MediaPipeline(source, (stage,), (False,), pipeline.attachments)


def lower_sr_pipeline(pipeline):
    pipeline = require_pipeline(pipeline)
    if len(pipeline.stages) != 1 or pipeline.stages[0].backend_id != OWNED_CSR1_SR.backend_id:
        raise ValueError("Connect one DLSS SR Stage to Pipeline Render")
    return MediaPipeline(pipeline.sequence_copy(), attachments=pipeline.attachments), pipeline.stages[0].parameters_copy()


def append_sl(pipeline, mode="quality", output_width=960, output_height=540, output_size_mode="manual"):
    pipeline = require_pipeline(pipeline)
    if pipeline.stages:
        raise ValueError("Connect Input Assembler directly; mixed or repeated Streamline stages are not implemented")
    source = pipeline.sequence_copy()
    from .sr_dimensions import output_size
    output_width, output_height = output_size(source['public']['width'],source['public']['height'],
        output_size_mode,output_width,output_height,dlaa=mode=='dlaa')
    stage = FeatureStage(OWNED_CXR1_SL.backend_id, "dlaa" if mode == "dlaa" else "sr",
                         {"mode": mode, "output_width": output_width, "output_height": output_height})
    return MediaPipeline(source, (stage,), (False,), pipeline.attachments)


def lower_sl_pipeline(pipeline):
    pipeline = require_pipeline(pipeline)
    if len(pipeline.stages) != 1 or pipeline.stages[0].backend_id != OWNED_CXR1_SL.backend_id:
        raise ValueError("Connect one DLSS Streamline Stage to Pipeline Render")
    return MediaPipeline(pipeline.sequence_copy(), attachments=pipeline.attachments), pipeline.stages[0].parameters_copy()


def pipeline_report(pipeline) -> dict:
    from .sr_dimensions import output_budget_report
    pipeline = require_pipeline(pipeline)
    sequence = pipeline.sequence_copy()
    settings = guide_settings(sequence["settings"])
    active = any(stage.parameters_copy().get("nr_enabled", True) and stage.parameters_copy().get("mix", 1) != 0
                 for stage in pipeline.stages)
    sr = bool(pipeline.stages and pipeline.stages[0].feature in {"sr", "dlaa"})
    sl = bool(pipeline.stages and pipeline.stages[0].backend_id == OWNED_CXR1_SL.backend_id)
    roles = {a.role for a in pipeline.attachments}
    available = roles | {"color", "motion", "timeline"}
    if not sl or "camera" in roles:
        available.add("frame_metadata")
    requirements = (requirements_report(pipeline.stages[0].backend_id, pipeline.stages[0].feature, available)
                    if pipeline.stages else None)
    source = sequence.get("input_report", {})
    return {
        "schema_version": 1, "kind": "media_pipeline_report",
        "state": "blocked" if source.get("ready") is False else "configured",
        "ready_for_execution": bool(pipeline.stages) and not sr and source.get("ready") is not False,
        "source": deepcopy(sequence.get("public", {})),
        "feature": pipeline.stages[0].feature if pipeline.stages else None,
        "output": ({"width": pipeline.stages[0].parameters_copy()["output_width"],
                    "height": pipeline.stages[0].parameters_copy()["output_height"]} if sr else None),
        "output_budget": (output_budget_report(pipeline.stages[0].parameters_copy()['output_width'],
                            pipeline.stages[0].parameters_copy()['output_height']) if sl else None),
        "missing_inputs": requirements["missing_required"] if requirements else [],
        "requirements": requirements,
        "color_policy": deepcopy(sequence.get("color_policy")),
        "motion": {"provider": settings.motion_provider, "settings": asdict(settings),
                   "semantics": "current_to_previous_pixels_top_left_xy",
                   "provenance": ("external_declared" if settings.motion_provider == "external" else
                                  "synthetic_zero" if settings.motion_provider == "zero" else "image_estimate"),
                   "usage": "required" if active else "deferred" if not pipeline.stages else "not_required_bypass"},
        "attachments": [{"role": item.role, "semantics": item.semantics,
                         "usage": ("required" if active else "deferred") if item.role == "motion" or (sr and item.role == "depth") or (sl and item.role == "camera")
                                  else "not_consumed_by_feature" if sr else "not_consumed_by_nr",
                         "provider": item.payload.report() if hasattr(item.payload, "report") else None}
                        for item in pipeline.attachments],
        "stages": [{"index": index + 1, "backend": stage.backend_id, "feature": stage.feature,
                    "parameters": stage.parameters_copy(), "inherited": pipeline.inherited[index]}
                   for index, stage in enumerate(pipeline.stages)],
        "issues": deepcopy(source.get("issues", [])),
        "validation": {"headers": source.get("state", "not_inspected"),
                       "timestamps": "deferred_to_requested_range", "content_hash": "deferred_to_execution",
                       "runtime": "cxr1_capability_handshake_required" if sl else "csr1_sdk_capability_handshake_required" if sr else "not_probed", "gpu": "not_executed"},
    }


def lower_nr_pipeline(pipeline, comparison=None, runtime=None) -> tuple[dict, dict]:
    """Translate to the current bounded executor without intermediate encoding."""
    pipeline = require_pipeline(pipeline)
    if not pipeline.stages:
        raise ValueError("Add a DLSS NR Stage before previewing or rendering the pipeline")
    if any(stage.feature != "nr" for stage in pipeline.stages):
        raise ValueError("This preview endpoint supports NR only; connect SR Stage to Pipeline Render")
    profiles = [stage.parameters_copy() for stage in pipeline.stages]
    sequence = pipeline.sequence_copy()
    comparison_profiles = compile_legacy_nr(comparison).legacy_parts()[0] if comparison is not None else []
    if all(not leaf.get("nr_enabled", True) or leaf.get("mix", 1) == 0 for leaf in profiles + comparison_profiles):
        # New pipeline only: all stages bypass, so no flow estimator is required.
        sequence["settings"].pop("flow_provider", None)
        sequence["settings"].pop("external_motion", None)
        sequence["settings"]["motion_provider"] = "zero"
        sequence.setdefault("public", {})["settings"] = deepcopy(sequence["settings"])
    profile = profiles[0] if len(profiles) == 1 else build_pass_stack(len(profiles), *profiles)
    if len(profiles) > 1:
        profile["inherited"] = list(pipeline.inherited)
    sequence["pipeline_report"] = pipeline_report(pipeline)
    if runtime is not None:
        from .processing_plan import resolve_nr_parts
        _, resolved = resolve_nr_parts(profile, runtime)
        for stage in sequence["pipeline_report"]["stages"]:
            stage["backend"] = resolved["backend_id"]
        sequence["pipeline_report"]["runtime_binding"] = "explicit_runtime_selection"
    sequence["pipeline_report"]["resolved_motion_provider"] = guide_settings(sequence["settings"]).motion_provider
    return sequence, profile
