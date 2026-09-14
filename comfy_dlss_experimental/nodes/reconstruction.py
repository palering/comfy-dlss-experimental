"""Thin Comfy adapters for the explicit CXR1 renderer-bundle path."""
import json
from pathlib import Path

from comfy_api.latest import io
from ..sr_dimensions import LIMITS

from ..sl_bundle import load_bundle
from .types import RuntimeConfig

ReconstructionBundleType = io.Custom("DLSSE_RECONSTRUCTION_BUNDLE")


class DLSSExperimentalReconstructionInput(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalReconstructionInput", display_name="DLSS Reconstruction Bundle Input",
            category="DLSS Experimental/Pipeline",
            description="Load explicit renderer color, motion, device depth, camera and optional RR buffers. This is not an ordinary MP4 input or a camera/depth estimator. Validates metadata and sizes without a Worker; pixel hashes are verified on each render read.",
            inputs=[io.String.Input("manifest_path", default="", tooltip="Absolute local path on the Comfy backend to a version-1 dlss_reconstruction_bundle JSON. Relative plane/audio files must remain inside that bundle directory.")],
            outputs=[ReconstructionBundleType.Output("bundle"), io.String.Output("report")],
            is_experimental=True)

    @classmethod
    def fingerprint_inputs(cls, manifest_path):
        # Re-read external manifest/files each queue submission, not a stale
        # identity cached by path. The render endpoint also reopens the manifest.
        return float("nan")

    @classmethod
    def execute(cls, manifest_path):
        from comfy.model_management import processing_interrupted
        if not isinstance(manifest_path, str) or not manifest_path or not Path(manifest_path).expanduser().is_absolute():
            raise ValueError("Use an absolute reconstruction manifest path on the Comfy backend")
        bundle = load_bundle(manifest_path, cancelled=processing_interrupted)
        return io.NodeOutput(bundle, json.dumps(bundle.report(), ensure_ascii=False, indent=2))


class DLSSExperimentalReconstructionRender(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalReconstructionRender", display_name="DLSS Reconstruction Render",
            category="DLSS Experimental/Processing",
            description="Execute SR/DLAA/RR using the separate owned_sl CXR1 runtime, with explicit renderer inputs. Outputs a standard VIDEO for Save Video and its preview. A one-frame range gives a frame preview. No foreground focus, FG/MFG, NR Look, automatic guide estimation or old CSR1 fallback.",
            inputs=[ReconstructionBundleType.Input("bundle", tooltip="Connect Reconstruction Bundle Input. RR needs the complete declared renderer/material buffer set."),
                    RuntimeConfig.Input("runtime", tooltip="Connect Runtime Configuration with an owned_sl preset and keep_worker_alive disabled. CNR1/CSR1 Workers cannot serve CXR1."),
                    io.Combo.Input("feature", options=["sr", "rr"], default="sr", tooltip="sr + dlaa mode is DLAA; rr + dlaa mode is native-resolution RR. RR requires linear SDR renderer color and genuine material buffers."),
                    io.Combo.Input("mode", options=["quality", "balanced", "performance", "ultra_performance", "dlaa"], default="quality", tooltip="DLAA keeps source dimensions. Other modes require explicit larger output dimensions compatible with the runtime's optimal-settings query."),
                    io.Int.Input("output_width", default=960, min=LIMITS['min_side'], max=LIMITS['output_max_side'], tooltip="Explicit output width; DLAA ignores this and keeps the input width. Even dimensions and exact source aspect ratio are required."),
                    io.Int.Input("output_height", default=540, min=LIMITS['min_side'], max=LIMITS['output_max_side'], tooltip="Explicit output height; DLAA keeps input height. No input or guide resizing is performed."),
                    io.Int.Input("start_frame", default=0, min=0, max=99999, tooltip="Zero-based first output frame in the bundle. Independent of NR Preview ranges."),
                    io.Int.Input("frame_count", default=0, min=0, max=100000, tooltip="0 outputs all remaining frames; 1 outputs one frame for inspection. A range beyond the bundle is rejected."),
                    io.Int.Input("history_frames", default=8, min=0, max=120, tooltip="Evaluate up to this many preceding bundle frames without encoding them. First evaluated frame resets history; explicit source cuts remain respected."),
                    io.Boolean.Input("include_audio", default=True, tooltip="Copy timing from the bundle's optional hash-bound audio source, aligned to timeline zero; AAC re-encoding is used. No audio is invented when absent."),
                    io.String.Input("ffmpeg_path", default="", optional=True, advanced=True, tooltip="Optional absolute host ffmpeg executable/directory; empty uses backend PATH."),
                    io.String.Input("ffprobe_path", default="", optional=True, advanced=True, tooltip="Optional absolute host ffprobe executable/directory; empty uses backend PATH or the configured ffmpeg directory.")],
            outputs=[io.Video.Output("video"), io.String.Output("report")],
            not_idempotent=True, is_experimental=True)

    @classmethod
    def execute(cls, bundle, runtime, feature="sr", mode="quality", output_width=960, output_height=540,
                start_frame=0, frame_count=0, history_frames=8, include_audio=True, ffmpeg_path="", ffprobe_path=""):
        from comfy_api.latest import InputImpl
        from ..comfy_adapter import comfy_execution_context
        from ..sl_execution import render_reconstruction
        output, report = render_reconstruction(context=comfy_execution_context(), bundle=bundle, runtime=runtime,
            feature=feature, mode=mode, output_width=output_width, output_height=output_height,
            start_frame=start_frame, frame_count=frame_count, history_frames=history_frames,
            include_audio=include_audio, media_tools_config={"ffmpeg_path": ffmpeg_path, "ffprobe_path": ffprobe_path})
        return io.NodeOutput(InputImpl.VideoFromFile(str(output)), json.dumps(report, ensure_ascii=False, indent=2))
