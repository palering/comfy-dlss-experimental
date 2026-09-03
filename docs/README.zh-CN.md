# comfy-dlss-experimental 文档

[English](README.en.md) · 简体中文

Audience: public

这里维护用户安装、已实现行为和工程契约；正文链接保持中文。英文由顶部切换进入。

## 建议阅读顺序

1. [安装与平台](distribution.zh-CN.md)：clone、helper、运行时和验证范围。
2. [外部文件准备](DLL_PREPARATION.zh-CN.md)与 [FFmpeg/PyAV](media-tools.zh-CN.md)。
3. [节点使用](comfy-video-nodes.zh-CN.md)：预览、处理与保存。
4. [示例工作流](../example_workflows/README.zh-CN.md)与[界面翻译](i18n.zh-CN.md)。

## 专题

- [输入适配](video-input-adapter.zh-CN.md)、[输入重建契约](nr-input-reconstruction.zh-CN.md)、[NVIDIA 光流](nvidia-optical-flow.zh-CN.md)。
- [NR Look](nr-look.zh-CN.md)、[预览性能与生命周期](preview-performance.zh-CN.md)。
- [架构](ARCHITECTURE.zh-CN.md)、[直接 NR 协议](direct-nr-relay.zh-CN.md)、[实验运行时边界](dlss5-runtime-research-2026-09.zh-CN.md)。
- [开发发布](DEVELOPMENT.zh-CN.md)、[C++ 质量](sidecar-cpp-quality.zh-CN.md)、[视频验证](video-nr-validation.zh-CN.md)。
- [Sidecar](../sidecar/README.zh-CN.md)、[诊断帧协议](../sidecar/protocol/frame-stream-v1.zh-CN.md)、[NVOF 头文件来源](../sidecar/vendor/nvof/README.zh-CN.md)。
- [文档维护规范](guide.zh-CN.md)。

## 公开与私有边界

实体文档以 .en.md / .zh-CN.md 配对，行为变更同步更新；无后缀入口只导航。法律原文不视为待翻译用户指南。原始计划、私有研究、凭据、本机路径、安全敏感细节和草稿不进入 docs。私有交接放本地被 Git 忽略的 .agent-docs/process.md。
