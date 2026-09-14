"""Input assembly on the left, deferred feature stages on the right."""
import json

from comfy_api.latest import io
from ..diagnostic_ui import diagnostic_ui

from ..media_pipeline import append_nr, assemble_input, pipeline_report, select_flow
from .types import MediaPipelineType, NRProfile, OpticalFlowProvider, TemporalSequence, TemporalSettings
from .external_guides import ExternalGuideType
from .streamline import CameraProviderType


class DLSSExperimentalFlowSelector(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalFlowSelector", display_name="DLSS Flow Selector",
            category="DLSS Experimental/Guides/Optical Flow",
            description="Select one provider branch before computation. Only the selected lazy input is requested. Missing or invalid selection fails; no silent fallback.",
            inputs=[io.Combo.Input("selection", options=["a", "b", "c"], default="a"),
                    OpticalFlowProvider.Input("a", optional=True, lazy=True),
                    OpticalFlowProvider.Input("b", optional=True, lazy=True),
                    OpticalFlowProvider.Input("c", optional=True, lazy=True)],
            outputs=[OpticalFlowProvider.Output("flow_provider"), io.String.Output("report")],
            is_experimental=True)

    @classmethod
    def check_lazy_status(cls, selection="a", a=None, b=None, c=None):
        if selection not in ("a", "b", "c"):
            raise ValueError("Choose optical-flow input a, b or c")
        return [selection] if {"a": a, "b": b, "c": c}[selection] is None else []

    @classmethod
    def execute(cls, selection="a", a=None, b=None, c=None):
        provider = select_flow(selection, a, b, c)
        return io.NodeOutput(provider, json.dumps({"selected": selection, "provider": provider}, indent=2))


class DLSSExperimentalInputAssembler(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalInputAssembler", display_name="DLSS Input Assembler",
            category="DLSS Experimental/Pipeline",
            description="Assemble inspected VIDEO and guides. Configured motion uses DIS/NVIDIA/zero settings; external mode reads numerical motion by source hash and PTS. Depth/normals/mask/confidence are retained and reported, not consumed by NR. No decoding, model execution or DLL loading here.",
            inputs=[TemporalSequence.Input("sequence"),
                    OpticalFlowProvider.Input("flow_provider", optional=True),
                    TemporalSettings.Input("settings", optional=True),
                    ExternalGuideType.Input("external_motion", optional=True, lazy=True),
                    ExternalGuideType.Input("depth", optional=True),
                    ExternalGuideType.Input("normals", optional=True),
                    ExternalGuideType.Input("mask", optional=True),
                    ExternalGuideType.Input("confidence", optional=True),
                    io.Combo.Input("motion_source", options=["configured", "external"], default="configured", optional=True),
                    CameraProviderType.Input("camera", optional=True, tooltip="Explicit source-bound camera timeline for Streamline SR/DLAA; not consumed by NR.")],
            outputs=[MediaPipelineType.Output("pipeline"), io.String.Output("report")],
            is_output_node=True, is_experimental=True)

    @classmethod
    def check_lazy_status(cls, motion_source="configured", external_motion=None, **kwargs):
        if motion_source not in ("configured", "external"):
            raise ValueError("Choose configured or external motion source")
        return ["external_motion"] if motion_source == "external" and external_motion is None else []

    @classmethod
    def execute(cls, sequence, flow_provider=None, settings=None, external_motion=None,
                depth=None, normals=None, mask=None, confidence=None, motion_source="configured", camera=None):
        from ..external_guides import ExternalGuideProvider
        attachments = []
        for role, provider in (("depth", depth), ("normals", normals), ("mask", mask), ("confidence", confidence)):
            if provider is not None:
                if not isinstance(provider, ExternalGuideProvider) or provider.role != role:
                    raise ValueError(f"Connect a numerical {role} guide to {role}")
                attachments.append(provider.to_attachment())
        if camera is not None:
            from ..camera_provider import CameraProvider
            if not isinstance(camera, CameraProvider):
                raise ValueError("Connect Camera Timeline Input to camera")
            attachments.append(camera.to_attachment())
        pipeline = assemble_input(sequence, flow_provider, settings, attachments,
                                  external_motion=external_motion, motion_source=motion_source)
        report = pipeline_report(pipeline)
        return io.NodeOutput(pipeline, json.dumps(report, ensure_ascii=False, indent=2),
                             ui=diagnostic_ui("dlss_pipeline_report", report))


class DLSSExperimentalNRStage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalNRStage", display_name="DLSS NR Stage",
            category="DLSS Experimental/Pipeline",
            description="Append NR Look/Stack to a pipeline without rendering or encoding. Stages may be chained or branched. Current backend supports at most three total NR passes and reuses source guides. Preview/Render triggers execution.",
            inputs=[MediaPipelineType.Input("pipeline"), NRProfile.Input("profile"),
                    io.Boolean.Input("enabled", default=True)],
            outputs=[MediaPipelineType.Output("pipeline"), io.String.Output("report")],
            is_output_node=True, is_experimental=True)

    @classmethod
    def execute(cls, pipeline, profile, enabled=True):
        result = append_nr(pipeline, profile, enabled)
        report = pipeline_report(result)
        return io.NodeOutput(result, json.dumps(report, ensure_ascii=False, indent=2),
                             ui=diagnostic_ui("dlss_pipeline_report", report))
