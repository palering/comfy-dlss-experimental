import json
from comfy_api.latest import io
from ..video_pipeline import profile_settings
from ..nr_options import LOOK_CHOICES, choice_id, legacy_labels
from .types import NRProfile


class DLSSExperimentalNRProfile(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DLSSExperimentalNRProfile", display_name="DLSS NR Look",
            category="DLSS Experimental/Controls", search_aliases=["dlss intensity", "nr profile"],
            description="NR look shared by Preview and Process. Experimental numeric preset/style IDs depend on the worker/model pair; not SR quality modes. After edits, Render preview again. Mix is a separate post-render blend. No external mask/depth input is transmitted.",
            inputs=[io.Boolean.Input("nr_enabled", display_name="启用 NR", default=True),
                    io.Float.Input("intensity", display_name="增强强度", default=0.25, min=0.0, max=3.0, step=0.01,
                                   tooltip="模型增强强度；不等同于 mix。0 不保证完全旁路，关闭 NR 或 mix=0 才是真正旁路。"),
                    io.Float.Input("mix", display_name="效果混合", default=1.0, min=0.0, max=1.0, step=0.01,
                                   tooltip="原图/NR 输出混合：0=原图且跳过 worker，1=完整 NR 输出。非模型参数。"),
                    # Append optional controls: existing node IDs, links and the
                    # first three serialized widget values remain compatible.
                    io.Combo.Input("nr_preset", display_name="模型预设（实验）", options=list(LOOK_CHOICES["nr_preset"]),
                                   default=LOOK_CHOICES["nr_preset"][0], optional=True, advanced=True,
                                   extra_dict={"dlss_legacy_labels": legacy_labels("nr_preset")},
                                   tooltip="建议使用模型默认。实验 A/B/C 尚无可靠效果命名，当前 DLL＋样片上输出相同；不是 SR 的质量/均衡档位。"),
                    io.Combo.Input("nr_style", display_name="画面风格", options=list(LOOK_CHOICES["nr_style"]),
                                   default=LOOK_CHOICES["nr_style"][1], optional=True,
                                   extra_dict={"dlss_legacy_labels": legacy_labels("nr_style")},
                                   tooltip="自然 / 电影感采用社区名称，不是跨 DLL 的效果保证。自然是旧工作流默认；切换后需重新 Render preview。"),
                    io.Float.Input("local_tone_strength", display_name="局部色调", default=1.0, min=0.0, max=3.0, step=0.01, optional=True,
                                   tooltip="局部色调强度，传给 NR 运行时；不是额外的浏览器调色滤镜。"),
                    io.Float.Input("local_structure_strength", display_name="结构与细节", default=1.0, min=0.0, max=3.0, step=0.01, optional=True,
                                   tooltip="结构/细节强度，传给 NR 运行时；拉高可能过度改变细节。"),
                    io.Float.Input("skin_structure_strength", display_name="皮肤结构（-1 自动）", default=-1.0, min=-1.0, max=3.0, step=0.01, optional=True,
                                   tooltip="原生皮肤结构参数；-1 保留运行时默认行为。没有附加皮肤锐化或外部皮肤蒙版，是否生效取决于 DLL 和素材。"),
                    io.Boolean.Input("automatic_mask", display_name="自动遮罩", default=False, optional=True,
                                     tooltip="请求运行时自动遮罩；不等于传入外部蒙版，也不保证各 DLL 都有可见差异。"),
                    io.Boolean.Input("ui_correction", display_name="UI 修正（实验）", default=False, optional=True, advanced=True,
                                     tooltip="实验性运行时 UI 修正。当前没有独立 UI 纹理，普通视频建议关闭；不能保证保护烧录字幕。"),
                    io.Combo.Input("worker_profile", display_name="运行时配置（实验）", options=list(LOOK_CHOICES["worker_profile"]),
                                   default=LOOK_CHOICES["worker_profile"][1], optional=True, advanced=True,
                                   extra_dict={"dlss_legacy_labels": legacy_labels("worker_profile")},
                                   tooltip="建议保留当前兼容配置。其余配置仅用于 worker 兼容性实验，不是画质档位；当前 DLL＋样片上未观察到差异。")],
            outputs=[NRProfile.Output("profile"), io.String.Output("profile_json")], is_experimental=True)

    @classmethod
    def validate_inputs(cls, nr_preset=0, nr_style=1, worker_profile=1):
        # Own validation only for these combos. Comfy continues validating all
        # other fields. Old headless/API workflows may still submit integers.
        try:
            for name, value in (("nr_preset", nr_preset), ("nr_style", nr_style), ("worker_profile", worker_profile)):
                choice_id(name, value)
        except ValueError as error:
            return str(error)
        return True

    @classmethod
    def execute(cls, nr_enabled, intensity, mix, nr_preset=0, nr_style=1,
                local_tone_strength=1.0, local_structure_strength=1.0,
                skin_structure_strength=-1.0, automatic_mask=False,
                ui_correction=False, worker_profile=1):
        profile = {"schema_version": 2, "nr_enabled": nr_enabled, "intensity": intensity, "mix": mix,
                   "nr_preset": choice_id("nr_preset", nr_preset), "nr_style": choice_id("nr_style", nr_style),
                   "local_tone_strength": local_tone_strength,
                   "local_structure_strength": local_structure_strength,
                   "skin_structure_strength": skin_structure_strength,
                   "automatic_mask": automatic_mask, "ui_correction": ui_correction,
                   "worker_profile": choice_id("worker_profile", worker_profile)}
        profile_settings(profile, 64, 64, 1, 120)
        return io.NodeOutput(profile, json.dumps(profile, indent=2))
