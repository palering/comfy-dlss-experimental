"""SR configuration/inspection nodes, deliberately separate from NR execution."""
import json

from comfy_api.latest import io
from ..diagnostic_ui import diagnostic_ui

from ..sr_contract import SRSettings, inspect_sr_attachments, plan_sr
from .types import MediaPipelineType, TemporalSequence


SRSettingsType = io.Custom("DLSSE_SR_SETTINGS")
MODE_LABELS = {"DLAA · 原分辨率抗锯齿": "dlaa", "Quality · 质量": "quality",
               "Balanced · 均衡": "balanced", "Performance · 性能": "performance",
               "Ultra Performance · 超高性能": "ultra_performance"}
PRESET_LABELS = {"Model default · 模型默认": "default", "Preset J · 预设 J": "j",
                 "Preset K · 预设 K": "k", "Preset L · 预设 L": "l", "Preset M · 预设 M": "m"}
JITTER_LABELS = {"Finished video: zero jitter · 成片零抖动": "unjittered_video",
                 "Actual render metadata · 原始渲染元数据": "external_render_metadata"}


def _choice(value, options):
    if value in options:
        return options[value]
    if value in options.values():
        return value
    raise ValueError("Unknown SR option")


class DLSSExperimentalSRSettings(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalSRSettings", display_name="DLSS SR / DLAA Settings",
            category="DLSS Experimental/Controls",
            description="Configure SR/DLAA separately from NR Look. Connect SR Stage then Pipeline Render with an SDK-enabled owned_sr runtime. Mode is an NGX quality choice, not a guaranteed scale factor. No jitter or device depth is fabricated from finished video; SR A/B Preview is not implemented.",
            inputs=[
                io.Combo.Input("mode", options=list(MODE_LABELS), default="Quality · 质量",
                               tooltip="SR targets a larger image; DLAA keeps source dimensions. This is not an NR style."),
                io.Combo.Input("preset", options=list(PRESET_LABELS), default="Model default · 模型默认",
                               tooltip="Model default is recommended. J/K/L/M are SDK preset hints, not cross-version image-quality guarantees."),
                io.Boolean.Input("depth_inverted", default=False,
                                 tooltip="Must match actual device-depth encoding; does not invert or convert a depth image."),
                io.Boolean.Input("auto_exposure", default=True,
                                 tooltip="Request NGX internal exposure. If disabled, supply positive frame exposure metadata; not an artistic brightness slider."),
                io.Combo.Input("jitter_policy", options=list(JITTER_LABELS),
                               default="Finished video: zero jitter · 成片零抖动",
                               tooltip="Finished video has no render sampling jitter. External jitter must describe the actual source render; never invent offsets."),
                io.Boolean.Input("motion_jittered", default=False, advanced=True,
                                 tooltip="Only enable for external engine vectors which actually include sampling jitter."),
                io.Boolean.Input("hdr", default=False, advanced=True,
                                 tooltip="Metadata declaration only; current SDR input conversion is not an HDR SR implementation."),
            ],
            outputs=[SRSettingsType.Output("settings"), io.String.Output("report")],
            is_experimental=True,
        )

    @classmethod
    def execute(cls, mode="Quality · 质量", preset="Model default · 模型默认", depth_inverted=False,
                auto_exposure=True, jitter_policy="Finished video: zero jitter · 成片零抖动",
                motion_jittered=False, hdr=False):
        settings = SRSettings(mode=_choice(mode, MODE_LABELS), preset=_choice(preset, PRESET_LABELS),
                              hdr=hdr, depth_inverted=depth_inverted, motion_jittered=motion_jittered,
                              auto_exposure=auto_exposure, jitter_policy=_choice(jitter_policy, JITTER_LABELS))
        return io.NodeOutput(settings.to_payload(), json.dumps(settings.to_payload(), ensure_ascii=False, indent=2))


class DLSSExperimentalSRPlan(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalSRPlan", display_name="DLSS SR Input Plan",
            category="DLSS Experimental/Pipeline",
            description="Inspect SR/DLAA extents and missing guides without decoding, running a model, or creating a Worker. Optional pipeline/sequence supplies inspected source dimensions; connect only one. Output is a report, not video or an executable NR stage.",
            inputs=[
                SRSettingsType.Input("settings"),
                io.Int.Input("input_width", default=640, min=64, max=1920,
                             tooltip="Manual source width; overridden by a connected inspected sequence or pipeline."),
                io.Int.Input("input_height", default=360, min=64, max=1080,
                             tooltip="Manual source height; overridden by a connected inspected sequence or pipeline."),
                io.Int.Input("output_width", default=1280, min=64, max=3840,
                             tooltip="Explicit SR target, preserving the exact aspect ratio. DLAA always uses the actual source size."),
                io.Int.Input("output_height", default=720, min=64, max=2160,
                             tooltip="This node does not resize source frames or guess a quality-mode scaling ratio."),
                TemporalSequence.Input("sequence", optional=True),
                MediaPipelineType.Input("pipeline", optional=True),
            ],
            outputs=[io.String.Output("report")], is_output_node=True, is_experimental=True,
        )

    @classmethod
    def execute(cls, settings, input_width=640, input_height=360,
                output_width=1280, output_height=720, sequence=None, pipeline=None):
        if sequence is not None and pipeline is not None:
            raise ValueError("Connect either a sequence or a pipeline to SR Input Plan, not both")
        parsed = SRSettings.from_payload(settings)
        source = None
        attachments = ()
        if pipeline is not None:
            from ..media_pipeline import require_pipeline
            connected = require_pipeline(pipeline)
            source = connected.sequence_copy()
            attachments = connected.attachments
        elif sequence is not None:
            # Reuse the existing non-decoding sequence validation boundary.
            from ..media_pipeline import assemble_input
            source = assemble_input(sequence).sequence_copy()
        if source is not None:
            public = source.get("public", {})
            input_width, input_height = public.get("width"), public.get("height")
        if parsed.mode == "dlaa":
            output_width, output_height = input_width, input_height
        report = plan_sr(parsed, input_width, input_height, output_width, output_height)
        report = inspect_sr_attachments(report, parsed, source, attachments)
        report["source_dimensions"] = "inspected_metadata" if source is not None else "manual_declaration"
        if source is not None:
            report["source_input_state"] = source.get("input_report", {}).get("state", "not_inspected")
            report["warnings"].append("Connected NR pipeline settings are inspected only; no NR stages or optical-flow providers run in this planner.")
        text = json.dumps(report, ensure_ascii=False, indent=2)
        return io.NodeOutput(text, ui=diagnostic_ui("dlss_sr_plan", report))


class DLSSExperimentalSRStage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalSRStage", display_name="DLSS SR / DLAA Stage",
            category="DLSS Experimental/Pipeline",
            description="Append one SR/DLAA stage to assembled input, then connect Pipeline Render and an owned_sr runtime. Requires numerical device depth and a Worker built with the official NGX SDK. NR/SR composition and SR A/B Preview are not implemented. No model executes in this node.",
            inputs=[MediaPipelineType.Input("pipeline"), SRSettingsType.Input("settings"),
                    io.Int.Input("output_width", default=1280, min=64, max=3840,
                                 tooltip="Explicit SR output width. DLAA keeps source dimensions and ignores this value."),
                    io.Int.Input("output_height", default=720, min=64, max=2160,
                                 tooltip="Explicit SR output height; preserve the source aspect ratio. DLAA keeps source dimensions.")],
            outputs=[MediaPipelineType.Output("pipeline"), io.String.Output("report")],
            is_output_node=True, is_experimental=True)

    @classmethod
    def execute(cls, pipeline, settings, output_width=1280, output_height=720):
        from ..media_pipeline import append_sr, pipeline_report
        result = append_sr(pipeline, settings, output_width, output_height)
        report = pipeline_report(result)
        return io.NodeOutput(result, json.dumps(report, ensure_ascii=False, indent=2),
                             ui=diagnostic_ui("dlss_pipeline_report", report))
