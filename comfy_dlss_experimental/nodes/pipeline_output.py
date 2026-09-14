"""Pipeline endpoints reuse established range, lifecycle, storage and preview behavior."""
from comfy_api.latest import io
from ..media_pipeline import lower_nr_pipeline, lower_sr_pipeline, lower_sl_pipeline, require_pipeline
from .types import MediaPipelineType, NRProfile, PreviewSession, RenderContract, RuntimeConfig
from .preview_session import DLSSExperimentalPreviewSession
from .process_video import DLSSExperimentalProcessVideo


class DLSSExperimentalPipelinePreview(DLSSExperimentalPreviewSession):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="DLSSExperimentalPipelinePreview",
            display_name="DLSS Pipeline Preview",
            category="DLSS Experimental/Preview",
            search_aliases=["dlss preview", "ab compare", "wipe", "look development"],
            description="生成处理链的节点内 A/B 预览，输出本次预览 VIDEO。保存 video_b/video_a 会保留本节点的时长、单帧模式及缩放。正式输出请从同一 NR Stage 分支连接 DLSS Pipeline Render → 保存视频。",
            inputs=[
                MediaPipelineType.Input("pipeline", tooltip="Connect NR Stage. Executes only the chosen preview range; no intermediate encoding."),
                RuntimeConfig.Input("runtime", tooltip="连接 Runtime Configuration，选择运行库、Proton 和 Worker 生命周期。"),
                NRProfile.Input("profile_a", display_name="对照效果 A（可选）", optional=True,
                                tooltip="不接时 A 是同尺寸、同色彩处理的输入对照；可接另一个 NR Look 或 Pass Stack 比较两套效果。这里不会自动冻结参数。"),
                io.Float.Input("start_time", default=0.0, min=0.0, max=86_400.0, step=0.1,
                               display_name="预览起点（秒）", tooltip="相对输入 VIDEO 起点的时间；若上游已裁剪，则从裁剪后的视频计时。例：10 表示从第 10 秒开始。"),
                io.Float.Input("duration", default=3.0, min=0.5, max=86_400.0, step=0.5,
                               display_name="手动区间长度（秒）", tooltip="勾选处理到视频末尾时忽略本值。否则片段模式实际生成这段长度，遇末尾缩短；单帧模式只用它限定游标范围。不是结束时间或预热时长。"),
                io.Combo.Input("preview_scale", options=[50, 75, 100], default=50,
                               display_name="预览尺寸（%）", tooltip="50 = 宽和高各缩至 50%（像素数约 1/4）；100 = 输入尺寸。它会改变实际 NR 输入和 VIDEO 输出，不只是界面缩放，也不是超分或效果强度。最终效果请用 100% 检查。"),
                RenderContract.Input("contract", optional=True, display_name="历史处理设置（可选）", tooltip="内部处理机制独立配置于 DLSS History Settings，不决定输出范围。默认前置历史 0.5 秒、新实例预热 120 次；不追加到输出时长。"),
                io.Combo.Input("preview_mode", options=["range", "frame"], default="frame", optional=True,
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
    def execute(cls, pipeline, **kwargs):
        sequence, profile = lower_nr_pipeline(pipeline, comparison=kwargs.get("profile_a"), runtime=kwargs.get("runtime"))
        kwargs.setdefault("preview_mode", "frame")
        return super().execute(sequence=sequence, profile_b=profile, **kwargs)


class DLSSExperimentalPipelineRender(DLSSExperimentalProcessVideo):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalPipelineRender", display_name="DLSS Pipeline Render",
            category="DLSS Experimental/Processing", search_aliases=["dlss process", "neural render video"],
            description="输出 NR、直接 CSR1 SR/DLAA 或 Streamline CXR1 SR/DLAA 为 VIDEO。Streamline 需要 owned_sl、相机及设备深度；输出尺寸由阶段设置。独立控制范围、历史及 Streamline 单帧输出，不读取 Preview 的范围或缩放。",
            inputs=[MediaPipelineType.Input("pipeline"), RuntimeConfig.Input("runtime"),
                    io.Float.Input("start_time", default=0.0, min=0.0, max=86400.0, step=0.1,
                                   display_name="处理起点（秒）", tooltip="相对输入 VIDEO 的起点；独立于预览起点。0 表示从输入开头处理。"),
                    io.Float.Input("duration", default=0.0, min=0.0, max=86400.0, step=0.1,
                                   display_name="处理时长（秒，0 = 剩余全部）",
                                   tooltip="勾选处理到视频末尾时忽略本值。否则正数决定处理时长，遇末尾缩短；兼容旧工作流：0 也表示剩余全部。不再限制为 30 秒，仍需足够磁盘空间。"),
                    io.Combo.Input("scale", options=[50, 75, 100], default=100,
                                   display_name="NR 输出尺寸（%）", tooltip="NR：100 保持输入尺寸，50/75 将宽高同时缩小。SR：必须为 100，目标尺寸由 SR Stage 指定；不是 SR 超分倍率。"),
                    RenderContract.Input("contract", optional=True, display_name="历史处理设置（可选）",
                                         tooltip="内部处理机制连接 DLSS History Settings；不决定输出时长。输出范围只由本节点的起点、时长或到末尾开关决定。"),
                    io.Boolean.Input("process_to_end", default=False, optional=True, display_name="处理到视频末尾",
                                     tooltip="自动读取输入 VIDEO，从处理起点输出到末尾，忽略手动时长；起点 0 = 完整视频。保持原尺寸请选择输出尺寸 100%。"),
                    io.Boolean.Input("retain_prepared_cache", default=True, optional=True, advanced=True,
                                     display_name="保留输入准备缓存",
                                     tooltip="在共享配额内保留小型准备缓存，方便调节 Look 时复用。大区间始终流式处理，不保存整段输入缓存。关闭后成功任务删除小缓存；失败或并发使用者要求保留时仍保留。旧的临时预览在 DLSS 缓存与文件管理中清理。"),
                    io.Boolean.Input("single_frame", default=False, optional=True,
                                     tooltip="Streamline only: output one frame at start_time after preceding history. NR single-frame inspection uses Pipeline Preview.")],
            outputs=[io.Video.Output("video"), io.String.Output("report")],
            not_idempotent=True, is_experimental=True)

    @classmethod
    def execute(cls, pipeline, **kwargs):
        pipeline = require_pipeline(pipeline)
        single_frame = kwargs.pop("single_frame", False)
        if pipeline.stages and pipeline.stages[0].backend_id == "owned_cxr1_sl":
            import json
            from ..sl_video import run_sl_process
            if kwargs.get("scale", 100) != 100:
                raise ValueError("Set Pipeline Render scale to 100 for Streamline; target dimensions are set by Streamline Stage")
            source, params = lower_sl_pipeline(pipeline)
            contract = kwargs.get("contract") or {"schema_version": 2, "mode": "native", "pre_roll": .5}
            if not isinstance(contract, dict) or contract.get("schema_version") != 2 or contract.get("mode") != "native":
                raise ValueError("Use current History Settings for Streamline pre-roll; NR warmup is not applied")
            video, report = run_sl_process(pipeline=source, runtime=kwargs["runtime"], **params,
                start_time=kwargs.get("start_time", 0), duration=kwargs.get("duration", 0),
                process_to_end=kwargs.get("process_to_end", False), pre_roll=contract.get("pre_roll", .5),
                single_frame=single_frame)
            return io.NodeOutput(video, json.dumps(report, ensure_ascii=False, indent=2))
        if single_frame:
            raise ValueError("Pipeline Render single_frame is Streamline-only; use Pipeline Preview for NR")
        if pipeline.stages and pipeline.stages[0].feature in {"sr", "dlaa"}:
            import json
            from ..sr_execution import run_sr_process
            if kwargs.get("scale", 100) != 100:
                raise ValueError("Set Pipeline Render scale to 100 for SR; target dimensions are set by SR Stage")
            source, params = lower_sr_pipeline(pipeline)
            contract = kwargs.get("contract") or {"schema_version": 2, "mode": "native", "pre_roll": .5}
            if not isinstance(contract, dict) or contract.get("schema_version") != 2 or contract.get("mode") != "native":
                raise ValueError("Use current History Settings for SR pre-roll; NR warmup is not applied to SR")
            video, report = run_sr_process(pipeline=source, runtime=kwargs["runtime"],
                settings=params["settings"], output_width=params["output_width"], output_height=params["output_height"],
                start_time=kwargs.get("start_time", 0), duration=kwargs.get("duration", 0),
                process_to_end=kwargs.get("process_to_end", False), pre_roll=contract.get("pre_roll", .5))
            return io.NodeOutput(video, json.dumps(report, ensure_ascii=False, indent=2))
        sequence, profile = lower_nr_pipeline(pipeline, runtime=kwargs.get("runtime"))
        return super().execute(sequence=sequence, profile=profile, **kwargs)
