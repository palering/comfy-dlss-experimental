# 实验运行时边界

[English](dlss5-runtime-research-2026-09.en.md) · 简体中文

Audience: public

状态：2026-09-03。本文说明本项目后端兼容性，不声称描述 NVIDIA 最新 SDK 发布状态。

## 当前实现

Comfy 节点使用实现 direct Feature 18 的外部 Windows Worker 与配套 NR 运行时。适配器交换 D5V2 颜色/运动帧；relay 只负责传输与进程监管，见[架构](ARCHITECTURE.zh-CN.md)和[外部文件](DLL_PREPARATION.zh-CN.md)。

本项目不内置或重编译该 Worker。二进制协议不是 NVIDIA 官方接口契约；相同文件名不足以证明兼容，应以版本化预设和内容哈希识别实际文件。

## 保留其他代码的原因

D3D12/ReShade carrier、合成 NGX bootstrap 和多平面 GPU 拷贝诊断用于隔离加载器、适配器、传输与资源生命周期问题。DLL 加载成功、发现 feature 导出或成功拷贝深度纹理，都不能证明 NR 已评估或消费了该深度。

这些不是默认处理路线，早期 bootstrap 限制也不能证明 NGX 普遍无法在 Proton 下运行。已跑通的 direct 路线不要求链接官方 NGX 静态 SDK，也不要求 Windows/MSVC 构建机。

## 证据边界

已在 RTX 4070 Ti SUPER、Linux 驱动 610.57.04、GE-Proton 11-6 与 Xwayland 上执行处理/光流测试。这只证明一套组合，不是最低驱动或全显卡支持表。Windows 代码路径和 helper 构建已有，GPU 实机验收未完成。原生 Linux NR、原生 Wayland 上的 direct NR 不受支持。

未来官方后端须按其公开 SDK 契约集成并测试；预留名称不是实现。不要从扩展点推断已支持 SR、FG、HDR、深度/材质输入或所有模型。

来源与分发边界见[第三方说明](../THIRD_PARTY_NOTICES.md)；诊断实现详见[直接中继](direct-nr-relay.zh-CN.md)及 [C++ 质量规则](sidecar-cpp-quality.zh-CN.md)。
