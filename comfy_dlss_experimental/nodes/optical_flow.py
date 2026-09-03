"""Lazy, connectable optical-flow providers (Comfy V3 schema)."""
from dataclasses import asdict
import json
from comfy_api.latest import io
from ..flow_provider import FlowProvider
from .types import OpticalFlowProvider

PRESETS = {"均衡 · Balanced": "balanced", "速度优先 · Fast": "fast", "质量优先 · Quality": "quality"}
GRIDS = {"4 × 4（较省资源）": 4, "2 × 2": 2, "1 × 1（逐像素输出）": 1}


def output(config):
    config.validate()
    payload = {"schema_version": 1, **asdict(config)}
    return io.NodeOutput(payload, json.dumps(payload, ensure_ascii=False, indent=2))


class DLSSExperimentalDISFlow(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="DLSSExperimentalDISFlow", display_name="DLSS Optical Flow · DIS",
            category="DLSS Experimental/Guides/Optical Flow", search_aliases=["dlss", "光流", "DIS"],
            description="CPU optical-flow provider. Connect to Video Guides; computes only requested video ranges. Quality uses a finer DIS analysis level, not a learned model.",
            inputs=[io.Combo.Input("preset", options=list(PRESETS), default="均衡 · Balanced", display_name="计算档位")],
            outputs=[OpticalFlowProvider.Output("flow_provider"), io.String.Output("settings_json")], is_experimental=True)

    @classmethod
    def execute(cls, preset="均衡 · Balanced"):
        return output(FlowProvider(kind="dis", preset=PRESETS[preset]))


class DLSSExperimentalNVIDIAFlow(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="DLSSExperimentalNVIDIAFlow", display_name="DLSS Optical Flow · NVIDIA",
            category="DLSS Experimental/Guides/Optical Flow", search_aliases=["dlss", "光流", "NVOFA"],
            description="Native NVIDIA hardware optical flow. Linux GPU-verified; Windows helper port awaits GPU validation. Requires the matching host helper and driver, not Proton/model weights. Never silently falls back to DIS.",
            inputs=[io.Combo.Input("preset", options=list(PRESETS), default="均衡 · Balanced", display_name="计算档位"),
                    io.Combo.Input("output_grid", options=list(GRIDS), default="4 × 4（较省资源）", display_name="输出网格",
                                   tooltip="Smaller grid means denser output, not a guarantee of better NR. Unsupported choices fail explicitly."),
                    io.Boolean.Input("temporal_hints", default=True, display_name="沿用相邻帧运动提示",
                                     tooltip="History is reset on cuts/new requests. Forward/backward estimates have separate histories."),
                    io.Int.Input("device", default=0, min=0, max=63, advanced=True, display_name="CUDA 设备编号")],
            outputs=[OpticalFlowProvider.Output("flow_provider"), io.String.Output("settings_json")], is_experimental=True)

    @classmethod
    def execute(cls, preset="均衡 · Balanced", output_grid="4 × 4（较省资源）", temporal_hints=True, device=0):
        return output(FlowProvider(kind="nvidia", preset=PRESETS[preset], output_grid=GRIDS[output_grid],
                                   device=device, temporal_hints=temporal_hints))
