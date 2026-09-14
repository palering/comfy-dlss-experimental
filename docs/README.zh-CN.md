# comfy-dlss-experimental 文档

[English](README.en.md) · 简体中文

Audience: public

这里维护用户安装、已实现行为和工程契约；正文链接保持中文。英文由顶部切换进入。

## 建议阅读顺序

1. [安装与平台](distribution.zh-CN.md)：clone、helper、运行时和验证范围。
2. [外部文件准备](DLL_PREPARATION.zh-CN.md)与 [FFmpeg/PyAV](media-tools.zh-CN.md)。
3. [项目与运行时目录](PROJECT_LAYOUT.zh-CN.md)：哪些属于 Git、用户数据和自动生成目录。
4. [节点使用](comfy-video-nodes.zh-CN.md)：预检、预览、处理与保存。
5. [示例工作流](../example_workflows/README.zh-CN.md)与[界面翻译](i18n.zh-CN.md)。

## 专题

- [输入组装、光流选择与 NR 处理链](media-pipeline.zh-CN.md)。
- [外部数值运动／深度引导](external-guides.zh-CN.md)（NR 消费所选运动，其他角色为显式附件）。
- [SR / DLAA 执行与验收限制](super-resolution.zh-CN.md)（可选 SDK Worker，默认构建未启用）。
- [Streamline 重建节点与渲染数据包格式](reconstruction-nodes.zh-CN.md)（显式 owned_sl/CXR1，与旧 SR 节点分离）。
- [VIDEO 接入 Streamline 与相机重建输入](streamline-video-input.zh-CN.md)（统一组装器、相机时间线、SR/DLAA 阶段和输出）。
- [显式相机 Streamline SR/DLAA/RR 后端](streamline-reconstruction.zh-CN.md)（CXR1 开发 API 与原生生命周期）。
- [独立文件执行与 Comfy 适配层](execution-boundary.zh-CN.md)。
- [自有 caller shim 开发](caller-shim.zh-CN.md)（不是当前 Worker）。
- [自有 Worker 验证宿主与 ABI 检查](owned-worker.zh-CN.md)（开发用途，非正式视频输出）。
- [自有 CNR1 流式接口与会话生命周期](owned-protocol.zh-CN.md)。
- [实验 owned_nr 安装与限制](owned-runtime.zh-CN.md)（显式选择，旧预设不变）。

- [存储上限、流式处理与文件清理](STORAGE.zh-CN.md)。

- [输入适配](video-input-adapter.zh-CN.md)、[输入重建契约](nr-input-reconstruction.zh-CN.md)、[NVIDIA 光流](nvidia-optical-flow.zh-CN.md)。
- [NR Look](nr-look.zh-CN.md)、[多层 NR](MULTI_PASS_NR.zh-CN.md)、[预览性能与生命周期](preview-performance.zh-CN.md)。
- [项目目录](PROJECT_LAYOUT.zh-CN.md)、[架构](ARCHITECTURE.zh-CN.md)、[运行时角色](RUNTIME_ROLES.zh-CN.md)、[直接 NR 协议](direct-nr-relay.zh-CN.md)、[实验运行时边界](dlss5-runtime-research-2026-09.zh-CN.md)。
- [开发发布](DEVELOPMENT.zh-CN.md)、[C++ 质量](sidecar-cpp-quality.zh-CN.md)、[视频验证](video-nr-validation.zh-CN.md)。
- [Sidecar](../sidecar/README.zh-CN.md)、[诊断帧协议](../sidecar/protocol/frame-stream-v1.zh-CN.md)、[NVOF 头文件来源](../sidecar/vendor/nvof/README.zh-CN.md)。
- [文档维护规范](guide.zh-CN.md)。

## 公开与私有边界

实体文档以 .en.md / .zh-CN.md 配对，行为变更同步更新；无后缀入口只导航。法律原文不视为待翻译用户指南。原始计划、私有研究、凭据、本机路径、安全敏感细节和草稿不进入 docs。私有交接放本地被 Git 忽略的 .agent-docs/process.md。
