# 自有 NR 实验运行时

[English](owned-runtime.en.md) · 简体中文

Audience: public

`owned_nr` 显式选择自有 CNR1 Worker；`direct_nr` 仍选择旧外部 D5V2 Worker。
不会自动迁移既有预设或工作流。旧 Preview/Process 节点与新 Pipeline 端点都遵循这一选择。

可选 `owned_sr` 预设现已接入同一个 Worker 的 SDK SR/DLAA 源码路径，与下述 NR
配置分开：不需要 NR caller，不支持 SR 常驻，尚未通过 SR GPU 验收。
详见[实验性 SR](super-resolution.zh-CN.md)。

## 文件与安装

仍按正常方式 clone 到 ComfyUI 的 `custom_nodes`，先准备现有[前置条件](distribution.zh-CN.md)。
此后端不需要 ReShade、RenoDX、Video Converter Worker、中继 EXE 或 systemd。

我们编译 `comfy-dlss-worker.exe` 和 `caller/nvngx.dll`。后者是薄库，不是其他项目的
同名可执行程序，也不是模型。用户另行提供匹配的 `nvngx_dlssnr.dll`，它不进入我们的辅助程序 ZIP。

1. 按[构建说明](owned-worker.zh-CN.md)构建 Worker/caller，或安装明确取得、声明
   `include_owned: true` 的辅助程序包。实现打包选项不等于已经发布自有 Worker 下载包。
2. 将[预设示例](../examples/runtime-presets/owned-nr.example.json)复制到节点用户数据目录的
   `runtime-presets/owned-nr.json`，填写路径。相对路径基于预设所在目录，不基于 Comfy 根目录。
3. Runtime Configuration 选择这个预设，backend 设为 `auto` 或 `owned_nr`。
   不覆盖原来的默认预设和组件。
4. Windows 直接执行 PE；Linux 选择已安装的 Proton，节点在数据根目录内管理专用
   `prefixes/owned-*`。Linux 原生 NR 尚不可用。Setup Helper 只读检查依赖；
   文件齐全不等于显卡／模型组合已通过实际推理验收。
5. 复用[处理链示例](../example_workflows/dlss_pipeline_preview.json)。预览独立控制范围；
   Pipeline Render → Save Video 决定正式输出。

推荐目录位于**节点自己的用户数据目录**，不是系统目录：

```text
runtime-presets/owned-nr.json
components/owned-nr/
  comfy-dlss-worker.exe          我们的程序
  caller/nvngx.dll              我们的薄库
  nvngx_dlssnr.dll              用户提供的模型／运行库
```

安装过对应辅助程序包时，前两个路径也可填 `@bundled/worker`、`@bundled/caller`；
NR 模型路径仍须明确配置。执行前会对三个文件做哈希校验和独立快照；过期描述及文件变化会报错。

## 限制与生命周期

- 媒体输入保持 SDR RGBA8。数值除以 255 转 FP16，不隐式应用 EOTF；有限输出
  截断到 [0,1]，每轮结束后量化回 RGBA8。**不代表 HDR 或全程 FP16 叠加链**。
- 当前自有传输接受 64–1920 × 64–1080，并不代表每种尺寸均通过模型实测。
  超限报错，不偷偷缩放。
- 旧 `worker_profile` 必须保留“当前兼容配置”对应的 1。其他外部 Worker 配置报错，
  不忽略；Look 控制通过 CNR1 传递。
- 仅在运行库、尺寸和控制参数一致时复用一个常驻 Worker。END 保留分配，下一任务
  明确重置历史。当前修改 Look 会重启受管 Worker。空闲 30–900 秒自动释放；
  手动释放、任务失败或取消会回收实例。长任务或多层流式任务使用立即释放模式。
  流式叠加每层独立维护历史，复用原始运动场。
- Linux 光流仍原生运行；NR 使用 Proton 不要求重复安装 Windows 光流 helper。

## 打包辅助程序

先构建目标平台光流 helper、旧 relay，以及自有 Worker/caller，然后执行：

```bash
python scripts/package_helpers.py --target linux-x86_64 --version YOUR_VERSION --include-owned
python install.py --archive /path/to/helpers.zip --sha256 TRUSTED_ARCHIVE_SHA256
```

Windows 包使用 `windows-x86_64`，其中光流 helper 是 Windows 版本；两个包里的
NR Worker/caller 都是相同的 Windows 目标。为兼容旧后端，包内仍包含 relay，
但 `owned_nr` 不启动它。旧的不含 `include_owned` 的包仍可安装。脚本不安装外部模型或驱动。

## 验证边界

Linux/GE-Proton、RTX 4070 Ti SUPER：640×360、8 帧的缓存／复用／流式结果一致；
双层流式叠加、取消和手动清理通过。真实隔离 Comfy API 测试加载 21 个节点，保存
处理后的 MP4，生成单帧预览，并在两次任务间复用 Worker。这是短样本验收，不等于
长视频稳定性、所有显卡、Windows GPU 或浏览器视觉验收。此后端未启用 SR/RR/FG。

## 独立 SR 绑定

SR 使用启用官方 SDK 构建的 `components/owned-sr/comfy-dlss-worker.exe`，以及用户
提供的 `components/owned-sr/nvngx_dlss.dll`。将 [SR 预设模板](../examples/runtime-presets/owned-sr.example.json)
配置为独立 `runtime-presets/owned-sr.json`，包含其项目 UUID。运行时快照仅包含
这两个文件；SR 不加载 `caller/nvngx.dll` 或 `nvngx_dlssnr.dll`。

默认 Zig NR Worker 会在 CSR1 握手中报告未编译 SR，因此现有 `--include-owned`
辅助程序包不承诺 SR 支持。[可选 MSVC/SDK 构建路线](super-resolution.zh-CN.md#运行时文件与构建)
与手动构建工作流只是源码功能，不代表已有公开二进制或 GPU 验收。SR 当前仅支持
任务后释放的流式处理，需要真实匹配的设备深度数值。保留正在使用的 NR 预设与模型文件。
