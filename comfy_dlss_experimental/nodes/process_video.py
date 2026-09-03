import json
from comfy_api.latest import io
from ..node_execution import run_process
from .types import NRProfile, RuntimeConfig, TemporalSequence, RenderContract


class DLSSExperimentalProcessVideo(io.ComfyNode):
    @classmethod
    def fingerprint_inputs(cls, **_kwargs):
        # Comfy temp files are not persistent cache artifacts.
        return float("nan")

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalProcessVideo", display_name="DLSS Process Video",
            category="DLSS Experimental/Processing", search_aliases=["dlss process", "neural render video"],
            description="独立控制正式处理范围，输出 VIDEO 连接保存视频。勾选处理到视频末尾可自动读取剩余时长，无需手填；取消原 30 秒限制，逐帧落盘并检查帧数和可用磁盘。本节点不读取 Preview 的范围或缩放，两者可共用输入、NR Look 和 Runtime。",
            inputs=[TemporalSequence.Input("sequence"), RuntimeConfig.Input("runtime"), NRProfile.Input("profile"),
                    io.Float.Input("start_time", default=0.0, min=0.0, max=86400.0, step=0.1,
                                   display_name="处理起点（秒）", tooltip="相对输入 VIDEO 的起点；独立于预览起点。0 表示从输入开头处理。"),
                    io.Float.Input("duration", default=0.0, min=0.0, max=86400.0, step=0.1,
                                   display_name="处理时长（秒，0 = 剩余全部）",
                                   tooltip="勾选处理到视频末尾时忽略本值。否则正数决定处理时长，遇末尾缩短；兼容旧工作流：0 也表示剩余全部。不再限制为 30 秒，仍需足够磁盘空间。"),
                    io.Combo.Input("scale", options=[50, 75, 100], default=100,
                                   display_name="输出尺寸（%）", tooltip="100 保持输入尺寸；50/75 将宽高同时缩小。它不受预览尺寸影响，也不是 DLSS 超分倍率。"),
                    RenderContract.Input("contract", optional=True, display_name="历史处理设置（可选）",
                                         tooltip="内部处理机制连接 DLSS History Settings；不决定输出时长。输出范围只由本节点的起点、时长或到末尾开关决定。"),
                    io.Boolean.Input("process_to_end", default=False, optional=True, display_name="处理到视频末尾",
                                     tooltip="自动读取输入 VIDEO，从处理起点输出到末尾，忽略手动时长；起点 0 = 完整视频。保持原尺寸请选择输出尺寸 100%。")],
            outputs=[io.Video.Output("video"), io.String.Output("report")],
            not_idempotent=True, is_experimental=True)

    @classmethod
    def execute(cls, sequence, runtime, profile, start_time, duration, scale, contract=None, process_to_end=False):
        contract = contract or {"schema_version": 2, "mode": "native", "warmup_frames": 120, "pre_roll": 0.5}
        video, report = run_process(sequence=sequence, runtime=runtime, profile=profile, contract=contract,
                                    start_time=start_time, duration=duration, scale=int(scale), process_to_end=process_to_end)
        return io.NodeOutput(video, json.dumps(report, indent=2))
