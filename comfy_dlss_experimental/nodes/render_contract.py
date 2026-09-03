import json
from comfy_api.latest import io
from .types import RenderContract


class DLSSExperimentalRenderContract(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="DLSSExperimentalRenderContract", display_name="DLSS History Settings",
                         category="DLSS Experimental/Controls", search_aliases=["warmup", "dlss history", "pre roll"],
                         description="内部时序处理机制，不决定输出视频长度。预热及前置历史只辅助 NR，不追加到输出；输出范围在 Preview/Process 中设置。当前连续逐帧处理，不按 duration 切批次。",
                         inputs=[io.Int.Input("warmup_frames", default=120, min=1, max=240,
                                              display_name="新实例预热次数", tooltip="新 Worker 重复首帧 NR 评估的次数，不是输出帧数、视频时长或批大小。兼容常驻实例复用初始化，不重复这些预热。"),
                                 io.Float.Input("pre_roll", default=0.5, min=0.0, max=5.0, step=0.1,
                                                display_name="前置历史（秒，不输出）", tooltip="输出起点前额外读取的真实视频上下文，帮助建立光流和 NR 历史，不进入保存视频。不会越过上游裁剪起点；不是每批处理长度。")],
                         outputs=[RenderContract.Output("contract"), io.String.Output("contract_json")], is_experimental=True)

    @classmethod
    def execute(cls, **kwargs):
        contract = {"schema_version": 2, "mode": "native", **kwargs}
        return io.NodeOutput(contract, json.dumps(contract, indent=2))
