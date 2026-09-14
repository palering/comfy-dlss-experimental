"""Explicit camera input and the Streamline stage for ordinary VIDEO pipelines."""
import json

from comfy_api.latest import io
from ..diagnostic_ui import diagnostic_ui

from ..camera_provider import load_camera_for_sequence
from ..media_pipeline import append_sl, pipeline_report
from .types import MediaPipelineType, TemporalSequence
from ..sr_dimensions import LIMITS, SIZE_MODES

CameraProviderType = io.Custom("DLSSE_CAMERA_PROVIDER")


class DLSSExperimentalCameraInput(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalCameraInput", display_name="DLSS Camera Timeline Input",
            category="DLSS Experimental/Guides/Camera",
            description="Load explicit calibrated, estimated or renderer camera metadata bound to this VIDEO source. No pose estimator runs and no camera is guessed. Input must cover every CFR source frame with unjittered projection, pose, temporal matrices and reset flags.",
            inputs=[TemporalSequence.Input("sequence", tooltip="Connect the same Video Input Adapter sequence used by Input Assembler and device depth."),
                    io.String.Input("manifest_path", default="", tooltip="Absolute backend path to a version-1 dlss_camera_timeline JSON bound to the exact source SHA-256.")],
            outputs=[CameraProviderType.Output("camera"), io.String.Output("report")],
            is_experimental=True)

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    def execute(cls, sequence, manifest_path):
        camera = load_camera_for_sequence(manifest_path, sequence)
        return io.NodeOutput(camera, json.dumps(camera.report(), ensure_ascii=False, indent=2))


class DLSSExperimentalStreamlineStage(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalStreamlineStage", display_name="DLSS Streamline SR / DLAA Stage",
            category="DLSS Experimental/Pipeline",
            description="Connect Input Assembler with camera and device depth, then Pipeline Render with owned_sl runtime. Reuses source color/motion/timing and the CXR1 executor. One SR/DLAA stage only; RR materials and NR/SL composition are not inferred from VIDEO.",
            inputs=[MediaPipelineType.Input("pipeline", tooltip="Input-only pipeline; requires explicit camera and device depth."),
                    io.Combo.Input("mode", options=["quality", "balanced", "performance", "ultra_performance", "dlaa"], default="quality", tooltip="DLAA keeps source dimensions; other modes require a larger explicit target supported by the runtime."),
                    io.Int.Input("output_width", default=960, min=LIMITS['min_side'], max=LIMITS['output_max_side'], tooltip="Manual output width only. Multiplier modes derive both dimensions from the current pipeline input."),
                    io.Int.Input("output_height", default=540, min=LIMITS['min_side'], max=LIMITS['output_max_side'], tooltip="Manual output height only; portrait uses the same pixel budget as landscape."),
                    io.Combo.Input("output_size_mode", options=list(SIZE_MODES), default="manual", optional=True,
                        tooltip="Scale relative to the actual SR input, recomputed when the input changes. Preserves exact aspect and rounds to the closest even grid. DLAA ignores this and stays 1x. Runtime quality-mode range is still checked; a multiplier is not a hardware support guarantee.")],
            outputs=[MediaPipelineType.Output("pipeline"), io.String.Output("report")],
            is_output_node=True, is_experimental=True)

    @classmethod
    def execute(cls, pipeline, mode="quality", output_width=960, output_height=540, output_size_mode="manual"):
        result = append_sl(pipeline, mode, output_width, output_height, output_size_mode)
        report = pipeline_report(result)
        return io.NodeOutput(result, json.dumps(report, ensure_ascii=False, indent=2),
                             ui=diagnostic_ui("dlss_pipeline_report", report))
