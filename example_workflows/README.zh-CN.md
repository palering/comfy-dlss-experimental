# 示例工作流

[English](README.en.md) · 简体中文

Audience: public

这些文件是可直接导入的 **Comfy 工作流 JSON**，不是 API prompt。将文件拖入
ComfyUI，或使用“打开”。视频源示例使用原生 Load Video，请把 `example-input.mp4`
换成自己的视频；重建示例则要求显式渲染数据包，不使用 Load Video。

| 文件 | 流程 | 建议的首次测试 |
| --- | --- | --- |
| [dlss_native_preview.json](dlss_native_preview.json) | 加载视频 → DIS 光流 → 视频引导 → 输入适配 → Runtime + Look + History → 预览 | 先检查输入，再只渲染游标单帧。 |
| [dlss_native_process.json](dlss_native_process.json) | 相同准备流程 → Process Video → Save Video | 先验证预览，再以 100% 尺寸排队处理全视频。 |
| [dlss_nvidia_flow_preview.json](dlss_nvidia_flow_preview.json) | 使用 NVIDIA 光流替换 DIS | 先确认原生 NVOF helper，再渲染一帧。 |
| [dlss_compare_looks.json](dlss_compare_looks.json) | 两套 NR Look 分别连接 A/B | 保持 A 作为参照，修改 B 强度，再渲染一帧。 |
| [dlss_pipeline_preview.json](dlss_pipeline_preview.json) | 光流选择 → 输入组装 → 两个 NR 阶段 → 处理链预览 | 实验性新连接；默认 DIS、第二阶段关闭、50% 单帧预览。 |
| [dlss_external_motion_preview.json](dlss_external_motion_preview.json) | 相同处理链，加外部数值引导 → Input Assembler external_motion | 默认已配置模式，不请求占位清单；准备真实清单后再切外部。 |
| [dlss_sr_experimental.json](dlss_sr_experimental.json) | 归一化输入 + 光流 + 外部设备深度 → SR Stage → Pipeline Render → Save Video | 仅实验接线；先准备匹配深度和启用 SDK 的 Worker，GPU 验收待完成。 |
| [dlss_reconstruction.json](dlss_reconstruction.json) | 渲染数据包输入 + owned_sl Runtime → 重建渲染 → 保存视频 | 填写真实数据包及运行库路径；默认只处理一帧 SR。支持 SR/DLAA/RR，见[格式与节点说明](../docs/reconstruction-nodes.zh-CN.md)。 |
| [dlss_streamline_video.json](dlss_streamline_video.json) | 输入适配 + 相机／深度 → 输入组装 → Streamline 阶段 → 处理链输出 → 保存视频 | 配置匹配素材的相机／深度清单和 owned_sl，默认短片段 SR；见[相机输入](../docs/streamline-video-input.zh-CN.md)。 |

NR 示例读取 `runtime-presets/default.json`。请先从
[直接 NR 预设模板](../examples/runtime-presets/direct-nr.example.json)
复制并配置它，同时安装或编译 relay。完整导出示例默认起点 0、时长 0、尺寸
100%，且勾选“处理到视频末尾”。预览示例默认 50% 游标单帧，便于快速迭代；
判断最终画质前应使用 100%，判断时序效果还需渲染短片段。

要在 NR 示例中试验多层 NR：添加 **DLSS NR Pass Stack**，把原来的 Look
接到第 1 层，先选 2 轮；可将第二套 Look 接到第 2 层，或留空继承第一层。然后把
Pass Stack 输出接到原先 Preview/Process 的 Look 插口。先用单帧比较一层/两层，
再检查运动短片；详见[多层 NR](../docs/MULTI_PASS_NR.zh-CN.md)。

仓库不包含 Worker、模型 DLL 或源视频。导入工作流不会自动保存到工作流库，
需要保留时请显式保存。

处理链示例分离素材检查、Guides 与效果阶段。选择器 `a` 为 DIS，`b` 需要 NVIDIA
helper。开启第二阶段可重复同一 Look，也可以另接不同 Look。正式输出时从最后阶段
分支连接 Pipeline Render 与保存视频；示例不预接该分支，避免意外渲染全视频。
参见[处理链说明与验证边界](../docs/media-pipeline.zh-CN.md)，旧示例不需要改线。

外部运动示例增加一个与组装节点绑定同一已检查序列的引导加载器。保持“运动来源 →
已配置”即可使用原 DIS 基线。将清单路径改为 ComfyUI 宿主上的真实文件后，再显式切换
为“外部”。所选清单缺失、不匹配或格式错误会报错，不会静默替换为 DIS。
示例不附带估计器、清单或数值帧文件，见[外部引导准备](../docs/external-guides.zh-CN.md)。

SR 示例改用 [SR 预设模板](../examples/runtime-presets/owned-sr.example.json)配置的
`runtime-presets/owned-sr.json`，需要本项目启用 SDK 的 Worker、用户提供的
`nvngx_dlss.dll`，以及与真实素材／网格／PTS 匹配的设备深度清单。深度占位路径不是
可运行输入。使用 sRGB 归一化、零抖动、自动曝光、任务后释放和 Render 尺寸 100%；
输出分辨率由 SR Stage 决定。尚无 SR A/B 预览或混合 NR/SR 栈。源码和模拟测试不等于
GPU 验收，详见 [SR 前置条件与构建](../docs/super-resolution.zh-CN.md)。
