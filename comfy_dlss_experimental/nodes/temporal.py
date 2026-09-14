import json
from comfy_api.latest import io
from ..diagnostic_ui import diagnostic_ui
from ..video_pipeline import guide_settings
from ..input_policy import InputColorPolicy
from ..input_inspection import inspect_video_input
from .types import TemporalSequence, TemporalSettings, OpticalFlowProvider


class DLSSExperimentalTemporalSettings(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalTemporalSettings", display_name="DLSS Video Guides",
            category="DLSS Experimental/Guides", search_aliases=["motion", "optical flow", "dlss"],
            description="Connect an optical-flow provider (DIS/NVIDIA). Owns analysis resolution, scene-cut resets and consistency diagnostics. Without a provider, retains legacy DIS/zero behavior. Prepares only requested ranges.",
            inputs=[io.Int.Input("analysis_scale", default=50, min=25, max=100, step=5,
                                tooltip="Optical-flow analysis width and height as a percentage of the source; not SR output scale. 50 uses one quarter as many analysis pixels, 100 analyzes the full source. DIS Balanced adds its own coarser pyramid level; DIS Quality uses the finest level. Changing this does not rerun geometry."),
                    io.Float.Input("scene_cut_threshold", default=0.35, min=0.01, max=1.0, step=0.01),
                    io.Float.Input("consistency_tolerance", default=2.5, min=0.1, max=20.0, step=0.1, advanced=True,
                                   tooltip="Forward/backward consistency diagnostic threshold, not model strength."),
                    io.Combo.Input("motion_provider", options=["dis", "zero"], default="dis", optional=True,
                                   advanced=True, display_name="未连接时的运动模式",
                                   tooltip="Used only when flow_provider is not connected. Zero still runs NR with temporal history."),
                    OpticalFlowProvider.Input("flow_provider", optional=True,
                                              tooltip="Connected provider takes precedence over the legacy motion mode.")],
            outputs=[TemporalSettings.Output("settings"), io.String.Output("settings_json")], is_experimental=True)

    @classmethod
    def execute(cls, **kwargs):
        settings = {"schema_version": 2, "motion_provider": "dis", **kwargs}
        parsed = guide_settings(settings)
        settings["motion_provider"] = parsed.motion_provider
        return io.NodeOutput(settings, json.dumps(settings, indent=2))


class DLSSExperimentalPrepareTemporalSequence(io.ComfyNode):
    @classmethod
    def fingerprint_inputs(cls, **_kwargs):
        return float("nan")  # Refresh cheap headers and visible diagnostics on explicit execution.

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalPrepareTemporalSequence", display_name="DLSS Video Input Adapter",
            category="DLSS Experimental/Guides", search_aliases=["prepare video", "temporal sequence"],
            description="Inspect video headers, explain compatibility and explicitly fill missing SDR color metadata. Does not transcode the whole video or run NR. Final decoding checks are deferred to the requested range.",
            inputs=[io.Video.Input("video"), TemporalSettings.Input("settings", optional=True),
                    io.Combo.Input("color_policy", options=["strict", "fill_missing"], default="strict", optional=True,
                                   tooltip="Strict requires known tags. Fill missing uses the assumptions below only where tags are absent; known HDR is never overridden."),
                    io.Combo.Input("assumed_transfer", options=["bt709", "iec61966-2-1"], default="bt709", optional=True,
                                   tooltip="Assumed transfer for missing tags only; iec61966-2-1 means sRGB."),
                    io.Combo.Input("assumed_range", options=["tv", "pc"], default="tv", optional=True,
                                   tooltip="Assumed missing range: tv=limited, pc=full. No source file is modified."),
                    io.Boolean.Input("normalize_to_srgb", default=False, optional=True, display_name="统一为 sRGB（SDR）",
                                     tooltip="Experimental: actually converts supported BT.709 input to sRGB before flow/NR; both comparison and result convert back on export. Already-sRGB passes through. Does not support HDR or override unknown tags; off preserves existing behavior."),
                    io.String.Input("ffmpeg_path", default="", optional=True, advanced=True,
                                    tooltip="Optional absolute host executable or bin directory. Blank uses the Comfy backend PATH. No quotes or command arguments; not a Proton path."),
                    io.String.Input("ffprobe_path", default="", optional=True, advanced=True,
                                    tooltip="Optional absolute ffprobe executable or bin directory. Blank uses the explicit FFmpeg directory, otherwise backend PATH. Invalid explicit paths never fall back.")],
            outputs=[TemporalSequence.Output("sequence"), io.String.Output("report")],
            is_output_node=True, is_experimental=True)

    @classmethod
    def execute(cls, video, settings=None, color_policy="strict", assumed_transfer="bt709", assumed_range="tv", normalize_to_srgb=False,
                ffmpeg_path="", ffprobe_path=""):
        from dataclasses import asdict
        from datetime import datetime, timezone
        from ..node_execution import interrupted
        settings = settings if settings is not None else {"schema_version": 2, "motion_provider": "dis"}
        guide_settings(settings)
        policy = InputColorPolicy(color_policy, assumed_transfer, assumed_range, normalize_to_srgb)
        policy.validate()
        from ..media_tools import resolve_media_tools
        tools_config = {"ffmpeg_path": ffmpeg_path, "ffprobe_path": ffprobe_path}
        tools = resolve_media_tools(tools_config)
        if tools["ffprobe"]["available"]:
            report = inspect_video_input(video, policy, settings.get("motion_provider", "dis"),
                                         cancelled=interrupted, ffprobe=tools["ffprobe"]["path"])
        else:
            report = {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
                      "ready": False, "state": "unreadable", "issues": [], "policy": asdict(policy),
                      "header_readable": False, "runtime_status": "not_probed",
                      "decode_validation": "not_run", "timing_validation": "not_run"}
        report["media_tools"] = tools
        if not tools["ready"]:
            report.update(ready=False, state="unreadable")
            for name in ("ffmpeg", "ffprobe"):
                if not tools[name]["available"]:
                    report.setdefault("issues", []).append({"kind": "media_tool", "message": tools[name]["error"]})
        active = report.get("active_view", {})
        public = {"width": active.get("width"), "height": active.get("height"), "duration": active.get("duration"),
                  "guide_state": "lazy_range_cache", "settings": settings}
        sequence = {"schema_version": 2, "video": video, "public": public, "settings": settings,
                    "color_policy": asdict(policy), "input_report": report, "media_tools_config": tools_config}
        return io.NodeOutput(sequence, json.dumps(report, ensure_ascii=False, indent=2), ui=diagnostic_ui("dlss_input_report", report))
