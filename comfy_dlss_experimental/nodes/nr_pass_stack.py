import json

from comfy_api.latest import io

from ..nr_effect import build_pass_stack
from .types import NRProfile


class DLSSExperimentalNRPassStack(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalNRPassStack",
            display_name="DLSS NR Pass Stack",
            category="DLSS Experimental/Controls",
            search_aliases=["dlss rounds", "multi pass nr", "nr cascade"],
            description="将 1–3 个 NR Look 按顺序叠加。每层读取上一层未压缩 RGBA 输出，复用输入适配器生成的原始运动引导，并重置自己的时序历史；不做中间视频编码。多层是非官方实验效果，不等于质量档位、SR 或 FG。",
            inputs=[
                io.Int.Input("pass_count", default=2, min=1, max=3, step=1,
                             display_name="NR 轮次",
                             tooltip="实际执行 1–3 次 Feature 18。耗时和生成式偏移通常随轮次增加；建议先用 2 层短片或单帧验证。"),
                NRProfile.Input("pass_1", display_name="第 1 层 Look",
                                tooltip="第一层效果配置，始终必需。"),
                NRProfile.Input("pass_2", display_name="第 2 层 Look（可选）", optional=True,
                                tooltip="未连接时继承上一层 Look；轮次小于 2 时忽略。"),
                NRProfile.Input("pass_3", display_name="第 3 层 Look（可选）", optional=True,
                                tooltip="未连接时继承上一层 Look；轮次小于 3 时忽略。三层属于高级实验。"),
            ],
            outputs=[NRProfile.Output("pass_stack"), io.String.Output("stack_json")],
            is_experimental=True,
        )

    @classmethod
    def execute(cls, pass_count, pass_1, pass_2=None, pass_3=None):
        stack = build_pass_stack(pass_count, pass_1, pass_2, pass_3)
        return io.NodeOutput(stack, json.dumps(stack, ensure_ascii=False, indent=2))
