# 示例工作流

[English](README.en.md) · 简体中文

Audience: public

这些文件是可直接导入的 **Comfy 工作流 JSON**，不是 API prompt。将文件拖入
ComfyUI，或使用“打开”。全部使用原生 Load Video/Save Video；请把
`example-input.mp4` 换成自己的上传或已选择视频。

| 文件 | 流程 | 建议的首次测试 |
| --- | --- | --- |
| [dlss_native_preview.json](dlss_native_preview.json) | 加载视频 → DIS 光流 → 视频引导 → 输入适配 → Runtime + Look + History → 预览 | 先检查输入，再只渲染游标单帧。 |
| [dlss_native_process.json](dlss_native_process.json) | 相同准备流程 → Process Video → Save Video | 先验证预览，再以 100% 尺寸排队处理全视频。 |
| [dlss_nvidia_flow_preview.json](dlss_nvidia_flow_preview.json) | 使用 NVIDIA 光流替换 DIS | 先确认原生 NVOF helper，再渲染一帧。 |
| [dlss_compare_looks.json](dlss_compare_looks.json) | 两套 NR Look 分别连接 A/B | 保持 A 作为参照，修改 B 强度，再渲染一帧。 |

示例统一读取 `runtime-presets/default.json`。请先从
[直接 NR 预设模板](../examples/runtime-presets/direct-nr.example.json)
复制并配置它，同时安装或编译 relay。完整导出示例默认起点 0、时长 0、尺寸
100%，且勾选“处理到视频末尾”。预览示例默认 50% 游标单帧，便于快速迭代；
判断最终画质前应使用 100%，判断时序效果还需渲染短片段。

要在任一现有示例中试验多层 NR：添加 **DLSS NR Pass Stack**，把原来的 Look
接到第 1 层，先选 2 轮；可将第二套 Look 接到第 2 层，或留空继承第一层。然后把
Pass Stack 输出接到原先 Preview/Process 的 Look 插口。先用单帧比较一层/两层，
再检查运动短片；详见[多层 NR](../docs/MULTI_PASS_NR.zh-CN.md)。

仓库不包含 Worker、模型 DLL 或源视频。导入工作流不会自动保存到工作流库，
需要保留时请显式保存。
