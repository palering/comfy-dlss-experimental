# 架构

[English](ARCHITECTURE.en.md) · 简体中文

Audience: public

ComfyUI 进程负责节点定义、输入检查、媒体准备、缓存键、任务调度与进程监管，绝不在 Python 中加载厂商图形 DLL。

## 当前视频路径

```text
Comfy VIDEO / Input Adapter
  → 缓存颜色准备 + 所选 DIS 或 NVIDIA 光流
  → direct_nr.py（D5V2 颜色/运动适配）
  → 已认证的回环 TCP（CLR1）
  → dlss-native-relay.exe
  → 用户提供的 nvngx.dll --video Worker + 匹配的 nvngx_dlssnr.dll
  → 校验 RGBA8 帧 → 宿主编码 → 预览 / 标准 VIDEO
```

Relay 只是进程与管道桥接器，不实现 NGX。外部 Worker 虽名为 DLL，实际是 Windows 可执行程序；任意同名驱动 DLL 或 caller shim 都不能替代其视频协议。详见[直接 NR 契约](direct-nr-relay.zh-CN.md)。

Python 负责解码、可选 SDR 工作传递函数转换、光流、切镜重置、前置帧、音频、编码与原子发布输出。小区间使用有配额的磁盘缓存；大区间先扫描时序，再逐帧执行解码/光流/NR/编码。修改 Look 可复用保留的小条目，大区间流式任务会重新计算光流，见[存储上限](STORAGE.zh-CN.md)。

可选 `DLSS NR Pass Stack` 在同一准备结果上顺序执行 1–3 层：下一层读取上一层未压缩
RGBA，所有层复用原始运动/时间戳/切镜标记，每层重置独立历史，最终只编码一次。
流式模式为每层使用独立 Worker，小型缓存模式保留整段逐层实现。这仍不是 SR/FG；详见
[多层 NR](MULTI_PASS_NR.zh-CN.md)。

宿主媒体程序的选择与 Proton 分离。Input Adapter 的路径配置沿 sequence 传入任务，通过任务局部上下文使用，不修改全局 PATH；缓存记录工具身份。PyAV 版本与外部命令分别报告，见[媒体工具](media-tools.zh-CN.md)。

## 平台边界

- Windows 直接启动 PE relay/Worker。
- Linux 使用用户选择的已安装 Proton 和隔离 prefix；当前 NR 路线需要 Steam 客户端目录与 Xwayland。
- macOS 是开发/测试宿主，不执行 NR。
- Runtime 平台必须与实际 Comfy 服务器匹配；Windows 不运行 Linux 发现、显示扫描或 /proc 检查。
- 可选 NVIDIA 光流 helper 是宿主原生程序：Linux ELF / Windows PE。驱动调用不经过 Proton，也不加载到 Comfy Python。

Linux 自动显示选择在 Wayland 桌面上仍使用 Xwayland。原生 Wayland 仅供诊断；原生 Linux NR 是会明确拒绝的预留选项，见[平台安装](distribution.zh-CN.md)。

## 运行时所有权与生命周期

预设绑定精确文件、配置与组件哈希；每次运行使用独立快照，不改源码或原始组件。DLL、Proton、尺寸或 NR 头部参数变化不能在已初始化实例中热切换。

默认按任务隔离并释放。可选常驻模式按需启动兼容 Worker/模型，串行执行任务，任务间重置历史，直到空闲超时、手动释放或配置不兼容。复用仅允许已验证的内容哈希对，损坏流被丢弃，见[生命周期与监控](preview-performance.zh-CN.md)。

Worker 崩溃只令任务失败，不把失败的原生状态带入 Comfy。Windows Job Object 和 Linux 标记进程清理限定所有权；禁止按通用名称杀掉无关 Wine 进程。子进程/prefix 是崩溃边界，**不是运行不可信二进制的安全沙箱**。

### Worker/shim 重构边界

当前外部 `nvngx.dll --video` 是完整控制台 Worker，不是 Zonnery `caller/nvngx.dll`
那种薄转发 DLL。后续自研后端把两者明确拆开：`comfy-dlss-worker.exe` 拥有设备、资源、
协议和 Feature 生命周期；可选 `caller/nvngx.dll` 只转发有类型的 Init/Create/Evaluate/
Release，且仅在所选运行库确实要求调用者验证时启用。当前 D5V2 后端在迁移期间保留，
不静默改变旧预设。完整二进制证据和设计原则见[运行时角色](RUNTIME_ROLES.zh-CN.md)。

## 媒体契约与未实现能力

当前 D5V2 接受 RGBA8 颜色和当前→上一帧的 RG16F 运动。光流是图像位移估计，不是引擎真实几何运动。此 Worker 接口不能传深度、法线、材质、任意蒙版或曝光纹理；SR、FG、HDR 未实现。出现某个实验 Look 字段不保证所有模型都对它有可见响应。

保留的 ReShade carrier、NGX bootstrap 和 [frame-stream-v1](../sidecar/protocol/frame-stream-v1.zh-CN.md) 属于独立诊断。多平面协议验证传输/D3D12 拷贝，不是 D5V2；其中的深度/蒙版不是当前 NR 输入。后续后端必须显式适配媒体语义、验证能力并拒绝不支持的输入。
