from __future__ import annotations

import json

from comfy_api.latest import io

from ..node_execution import run_preview
from .types import NRProfile, PreviewSession, RenderContract, RuntimeConfig, TemporalSequence


class DLSSExperimentalPreviewSession(io.ComfyNode):
    @classmethod
    def fingerprint_inputs(cls, **_kwargs):
        # Each explicit preview click needs a fresh session/event stream;
        # media guides have their own content-addressed cache underneath.
        return float("nan")

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="DLSSExperimentalPreviewSession",
            display_name="DLSS Preview Session",
            category="DLSS Experimental/Preview",
            search_aliases=["dlss preview", "ab compare", "wipe", "look development"],
            description="生成节点内 A/B 预览，并输出本次预览 VIDEO。保存 video_b/video_a 会保留本节点的时长、单帧模式及缩放，不会自动变成完整原尺寸视频。正式输出建议用独立的 DLSS Process Video → 保存视频。",
            inputs=[
                TemporalSequence.Input("sequence", tooltip="连接 DLSS Video Input Adapter 的 sequence；包含输入 VIDEO、色彩解释和光流引导设置。"),
                RuntimeConfig.Input("runtime", tooltip="连接 Runtime Configuration，选择运行库、Proton 和 Worker 生命周期。"),
                NRProfile.Input("profile_b", display_name="当前效果 B", tooltip="连接正在调节的 NR Look 或 Pass Stack。video_b 输出最终层预览。"),
                NRProfile.Input("profile_a", display_name="对照效果 A（可选）", optional=True,
                                tooltip="不接时 A 是同尺寸、同色彩处理的输入对照；可接另一个 NR Look 或 Pass Stack 比较两套效果。这里不会自动冻结参数。"),
                io.Float.Input("start_time", default=0.0, min=0.0, max=86_400.0, step=0.1,
                               display_name="预览起点（秒）", tooltip="相对输入 VIDEO 起点的时间；若上游已裁剪，则从裁剪后的视频计时。例：10 表示从第 10 秒开始。"),
                io.Float.Input("duration", default=3.0, min=0.5, max=86_400.0, step=0.5,
                               display_name="手动区间长度（秒）", tooltip="勾选处理到视频末尾时忽略本值。否则片段模式实际生成这段长度，遇末尾缩短；单帧模式只用它限定游标范围。不是结束时间或预热时长。"),
                io.Combo.Input("preview_scale", options=[50, 75, 100], default=50,
                               display_name="预览尺寸（%）", tooltip="50 = 宽和高各缩至 50%（像素数约 1/4）；100 = 输入尺寸。它会改变实际 NR 输入和 VIDEO 输出，不只是界面缩放，也不是超分或效果强度。最终效果请用 100% 检查。"),
                RenderContract.Input("contract", optional=True, display_name="历史处理设置（可选）", tooltip="内部处理机制独立配置于 DLSS History Settings，不决定输出范围。默认前置历史 0.5 秒、新实例预热 120 次；不追加到输出时长。"),
                io.Combo.Input("preview_mode", options=["range", "frame"], default="range", optional=True,
                               display_name="排队模式", tooltip="Comfy 顶部运行按钮使用此设置：range = 整个预览区间；frame = 游标处一帧。卡片内两个渲染按钮各自指定本次模式，不修改这里的保存值，也不触发下游保存节点。"),
                io.Float.Input("cursor_time", default=0.0, min=0.0, max=86_400.0, step=0.01, optional=True,
                               display_name="游标偏移（区间内秒）", tooltip="相对预览起点的偏移，必须小于区间长度。起点 10、偏移 1.5 表示输入 VIDEO 第 11.5 秒处的帧（按帧时间对齐）。仅单帧渲染使用；片段渲染不受游标影响。"),
                io.Boolean.Input("process_to_end", default=False, optional=True, display_name="处理到视频末尾",
                                 tooltip="自动读取输入 VIDEO 时长，从预览起点处理到末尾，忽略手动区间长度；起点 0 = 完整视频。单帧按钮仍只输出游标帧。大区间流式处理，保留帧数及磁盘配额检查。"),
                io.Boolean.Input("retain_prepared_cache", default=True, optional=True, advanced=True,
                                 display_name="保留输入准备缓存",
                                 tooltip="在共享配额内保留小型准备缓存，方便调节 Look 时复用。大区间始终流式处理，不保存整段输入缓存。关闭后成功任务删除小缓存；失败或并发使用者要求保留时仍保留。旧的临时预览在 DLSS 缓存与文件管理中清理。"),
            ],
            outputs=[PreviewSession.Output("session"), io.String.Output("status"),
                     io.Video.Output("video_b"), io.Video.Output("video_a")],
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
            is_output_node=True,
            not_idempotent=True,
            is_experimental=True,
        )

    @classmethod
    def execute(cls, **kwargs) -> io.NodeOutput:
        from server import PromptServer
        metadata = cls.hidden.extra_pnginfo or {}
        public, video_b, video_a = run_preview(
            sequence=kwargs["sequence"],
            runtime=kwargs["runtime"],
            contract=kwargs.get("contract") or {"schema_version": 2, "mode": "native", "warmup_frames": 120, "pre_roll": 0.5},
            profile_a=kwargs.get("profile_a"),
            profile_b=kwargs["profile_b"],
            start_time=kwargs["start_time"],
            duration=kwargs["duration"],
            preview_scale=int(kwargs["preview_scale"]),
            preview_mode=kwargs.get("preview_mode", "range"),
            cursor_time=kwargs.get("cursor_time", 0.0),
            process_to_end=kwargs.get("process_to_end", False),
            retain_prepared_cache=kwargs.get("retain_prepared_cache", True),
            node_id=str(cls.hidden.unique_id),
            client_id=getattr(PromptServer.instance, "client_id", None),
            workflow_id=str(metadata.get("workflow", {}).get("id", "")),
        )
        return io.NodeOutput(public, json.dumps(public, ensure_ascii=False, indent=2), video_b, video_a,
                             ui={"dlss_preview": [public]})


class DLSSExperimentalCompareVideo(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="DLSSExperimentalCompareVideo",
            display_name="DLSS Compare Video (Scaffold)",
            category="DLSS Experimental/Diagnostics",
            search_aliases=["video compare", "wipe", "flicker", "difference"],
            description="Will export synchronized A/B comparison videos after the sidecar output path exists.",
            inputs=[
                io.Video.Input("video_a"),
                io.Video.Input("video_b"),
                io.Combo.Input("mode", options=["side_by_side", "wipe", "flicker", "difference"], default="side_by_side"),
                io.Int.Input("split_percent", default=50, min=0, max=100, step=1),
            ],
            outputs=[io.Video.Output("comparison"), io.String.Output("report")],
            is_experimental=True,
        )

    @classmethod
    def execute(cls, **_kwargs) -> io.NodeOutput:
        return io.NodeOutput(block_execution="Comparison export requires the processed-video transport and is not implemented yet.")
