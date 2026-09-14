# comfy-dlss-experimental

[English](README.en.md) · 简体中文

Audience: public

用于研究**离线视频神经渲染（NR）**的 ComfyUI 自定义节点，提供节点内 A/B 预览与独立正式导出流程。

> **目前是源码预览版。** clone 只安装节点源码，不代表 NR 已能运行。
> 还需本项目 helper，以及可信、相互匹配的外部 Worker 与模型 DLL。
> **目前尚未发布预编译 helper Release 包**。Linux/Proton 已进行 GPU 实测；
> Windows 代码和 CI 已有，但 Windows GPU 实机验收仍待完成。

下面的快速开始保留旧 `direct_nr` 路线。可选的[自有 `owned_nr` 运行时](docs/owned-runtime.zh-CN.md)
以我们的 Worker 与薄 caller 替代外部 Worker，仍需用户提供 NR 模型。
[实验性 SR/DLAA 执行链](docs/super-resolution.zh-CN.md)的源码也已接入，但需要另行
链接 SDK 的 Worker 和设备深度数值输入，该 CSR1 节点路径**尚未通过 GPU 验收**。
独立 [Streamline 重建节点](docs/reconstruction-nodes.zh-CN.md)使用 `owned_sl`／CXR1
与显式相机／渲染数据包；SR、DLAA、RR、RR+DLAA 已通过有界 GPU、文件执行及 Comfy
SaveVideo 测试，也包含单帧输出。这不代表普通 MP4 已具备 RR 输入，也不是自然视频画质验收。

统一 [VIDEO → Streamline 路径](docs/streamline-video-input.zh-CN.md)也已连接输入组装器、
显式相机／深度、Streamline 阶段和处理链输出。SR/DLAA、DIS、历史单帧及音频区间已通过
有界 Linux GPU 和实际 Comfy SaveVideo 验收；相机估计器本身仍由外部提供。

[快速开始](#quick-start) · [前置条件](#前置条件) ·
[项目目录](#project-layout) · [自行构建](#build-from-source) ·
[示例工作流](#示例工作流)

<a id="quick-start"></a>

## Quick Start / 快速开始

最短测试路径是 **DIS 光流 → 单帧预览 → 全视频导出**。
先满足下面的前置条件；仅 clone 源码还不能执行 NR。

1. **Clone 节点**，在已有 ComfyUI 根目录执行：

   ```sh
   git clone https://github.com/palering/comfy-dlss-experimental.git custom_nodes/comfy-dlss-experimental
   ```

2. **检查依赖**：Comfy 的 Python 中需要 numpy、支持 DIS 的 cv2、av、PIL；
   宿主另需 ffmpeg 和 ffprobe。
   [Linux/Windows 具体命令](docs/distribution.zh-CN.md#第二步确认正在使用-comfy-的-python) ·
   [FFmpeg 官方下载页面](https://ffmpeg.org/download.html) ·
   [可选 pip 安装与路径配置](docs/media-tools.zh-CN.md#pip-installation)。

3. **准备本项目 relay**：[自行构建](#build-from-source)，或按
   [install.py 说明](docs/distribution.zh-CN.md#第四步安装本项目-helper)安装可信 helper ZIP。
   目前尚无公开预编译 Release；使用 DIS 不需要 NVOF helper。

4. **准备配套外部文件**：`nvngx.dll`（视频 Worker 可执行程序）和
   `nvngx_dlssnr.dll`（匹配模型）。放进独立版本目录，将
   [预设模板](examples/runtime-presets/direct-nr.example.json)复制到节点数据根的
   `runtime-presets/default.json`，填写文件路径。
   默认数据根：`ComfyUI/user/default/comfy-dlss-experimental/`。
   [文件来源、哈希与具体放置方式](docs/DLL_PREPARATION.zh-CN.md)。

5. **重启 Comfy、刷新页面、导入** [DIS 预览](example_workflows/dlss_native_preview.json)。
   在“加载视频”选择素材；“运行时配置”选择预设，Linux 下再选择已安装的 Proton。
   “视频输入适配”按需填写媒体工具路径，点击“检查输入”，处理报告中的输入/色彩问题。

   如需完整预检，添加 **DLSS 配置与环境助手**，连接“运行时配置”和“视频输入适配”输出，
   点击“检查所选配置”。它原样透传 Runtime，可继续连接 Preview/Process。

6. **预览与保存**：调整“NR 效果”，在 A/B 预览卡片点击“渲染当前帧”。
   再检查一小段视频；正式导出打开[完整视频导出](example_workflows/dlss_native_process.json)：
   **处理视频 → 保存视频**，输出尺寸 100、起点 0，勾选“处理到视频末尾”。

预览和正式处理范围互相独立：保存 Preview 的 `video_b` 只保存该预览的帧/片段及尺寸。
判断最终分辨率下的效果请使用 100% 尺寸。
[完整安装说明](docs/distribution.zh-CN.md)和[工作流指南](example_workflows/README.zh-CN.md)
另有 portable Python、自定义数据目录与排错说明。

## 功能

- 在节点卡片内渲染游标处单帧或选定视频区间。
- 滑动、并排、交替与差异对比：原始输入对结果，或两套 NR 效果。
- **NR Look** 调节增强强度、风格和实验参数。
- **NR Pass Stack** 将 1–3 个 Look 无损级联，复用原始运动引导且只编码最终结果。
- 可连接的 **DIS CPU 光流**与可选 **NVIDIA 硬件光流**。
- 导入[外部数值光流](docs/external-guides.zh-CN.md)，校验素材／哈希／时间；可附加深度、法线、蒙版和置信度并明确标注是否消费。
- 独立配置[实验性 SR / DLAA](docs/super-resolution.zh-CN.md)：SR Input Plan 检查输入，SR Stage → Pipeline Render 提供 SDK 执行链，GPU 验收待完成。
- 视频信息检查、显式解释缺失色彩标签、可选 SDR → sRGB 工作空间转换。
- **配置与环境助手**汇总校验已选运行时、组件哈希、媒体/Python 依赖、平台桥接和实际光流配置。
- 独立缓存颜色/光流准备结果；改 Look 不重复准备相同输入。
- 按任务释放或懒加载常驻 Worker，查看耗时、资源、记录并手动释放。
- 输出标准 **VIDEO**，连接 ComfyUI 原生 **保存视频 / Save Video**。

**尚未实现：**FG 补帧、HDR 处理、混合 NR/SR 栈、SR A/B 预览、NR 消费深度／法线／蒙版／置信度、
材质重建、原生 Linux NR、对比分屏视频导出。部分实验参数可能在某些 Worker/模型上无可见响应。

**实验性 FG 需要前台焦点：**当前 Present 捕获测试要求 **GPU 主机上的 Worker 窗口**
保持焦点，不是要求 ComfyUI 浏览器置顶。后台 FG 与 Comfy FG 节点尚不可用；NR 及已测试的
SR／DLAA／RR 重建路径不需要前台焦点。详见
[FG 窗口要求与暂停选项](docs/streamline-reconstruction.zh-CN.md#fg-窗口焦点与暂停选项)。

## 前置条件

| 项目 | 要求 |
| --- | --- |
| ComfyUI | 较新版本，具备 V3 API、原生 LoadVideo/SaveVideo、自定义节点翻译与 DOM 控件；使用 Comfy 自己的 Python，3.11 以上。 |
| 显卡与运行库 | 支持的 NVIDIA 显卡和驱动，以及匹配该组合的外部 NR Worker/模型；不是只更新驱动即可。 |
| Python 媒体依赖 | Comfy 环境内有 numpy、支持 DIS 的 cv2、av、PIL；本节点不自动补装。 |
| 宿主工具 | 宿主 ffmpeg、ffprobe 两个程序：[官方下载安装入口](https://ffmpeg.org/download.html)，或[可选 pip 方式](docs/media-tools.zh-CN.md#pip-installation)。后端 PATH 或 Input Adapter 指定路径；仅安装 PyAV 不等于有这两个命令。 |
| 本项目 helper | dlss-native-relay.exe；使用 NVIDIA 光流时还需要对应宿主平台的 NVOF helper。 |
| 输入与磁盘 | 当前支持 SDR、恒定帧率、方形像素；准备帧与输出需要足够磁盘。不支持的输入明确报错。 |

### 平台差异

- **Linux x86-64：**已安装的 Proton、Steam 客户端目录、可用的 Xwayland/`DISPLAY`。
  Wayland 桌面使用 Xwayland 即可，不需要切换为 X11 桌面。
  Runtime 中可下拉选择 Proton，或直接填写安装目录/启动器路径。
- **Windows x86-64：**自动识别宿主后直接启动 Windows Worker，不需要 Proton。
  已有代码和 CI，**尚未完成 Windows GPU 实机验收**。
- **macOS/ARM：**目前不支持实际执行 NR。

已测 Linux 组合：RTX 4070 Ti SUPER、驱动 610.57.04、GE-Proton 11-6。
它不是最低版本要求，也不是所有显卡的兼容保证。
详见[安装、平台与分发](docs/distribution.zh-CN.md)。

### 需要的外部 DLL

当前直接 NR 路线需要用户提供的应用组件只有 **`nvngx.dll` 视频 Worker**
和**配套的 `nvngx_dlssnr.dll` 模型**。已测组合来自 Video Converter v0.1.0 RTX40 包；
其中 Worker 不是同名驱动 DLL，也不是 caller shim。
**不需要 ReShade、RenoDX、Streamline 或 converter 网页服务。**

[DLL 专项文档](docs/DLL_PREPARATION.zh-CN.md)已列出来源、包内位置、SHA-256、显卡验证范围、
放置目录、预设配对以及可选光流的驱动库。运行时文件放源码外；
`COMFY_DLSS_HOME` 可覆盖默认数据根。

#### 已下载文件在当前后端中的结论

| 文件 | 当前结论 |
| --- | --- |
| Video Converter `bin/runtime/nvngx.dll` | **必需的外部文件。** 虽然后缀是 `.dll`，实际是接收 D5V2 视频流的 Worker 可执行程序。 |
| 与它配套的 Video Converter `nvngx_dlssnr.dll` | **必需的外部文件。** 必须与 Worker 配套；同名 RenoDX/Discord 文件不能自动视为可互换。 |
| `dlss-native-relay.exe` | **必需的本项目 helper。** 从本仓库源码构建，不是 NVIDIA 下载文件，也不是把某个 DLL 改名而来。 |
| `dlss-nvof-helper[.exe]` | **可选的本项目 helper。** 只有 NVIDIA 光流需要；DIS 不用。 |
| ReShade `dxgi.dll`、`renodx-dlss5*.addon64` | 当前 `direct_nr` 后端**没有使用**；只保留为研究路线材料。 |
| `nvngx_dlss.dll`、`D3DCompiler_47.dll`、`sl.*.dll`、独立 caller shim | **`direct_nr` 不使用。**可选 `owned_sr` 以启用 SDK 的 Worker 使用 `nvngx_dlss.dll`，`owned_nr` 使用我们的薄 caller；不要把这些独立功能的绑定混入快速开始的 NR 配对。 |

[外部文件专项文档](docs/DLL_PREPARATION.zh-CN.md)把 Zonnery 播放器和 DLSS5 Video
Converter 的不同文件要求逐项映射到我们的后端，并区分三种身份完全不同的 `nvngx.dll`。

<a id="project-layout"></a>

## 项目目录

```text
ComfyUI/
├── custom_nodes/comfy-dlss-experimental/       # Git checkout：源码/UI/测试/模板
└── user/default/comfy-dlss-experimental/       # 用户数据，不属于 Git
    ├── components/nr/<bundle>/
    │   ├── nvngx.dll                           # 外部 Worker
    │   └── nvngx_dlssnr.dll                    # 匹配的外部模型
    ├── runtime-presets/default.json             # 用户选择的绑定
    ├── prepared-clips/                          # 自动生成的颜色/运动缓存
    ├── runtime-snapshots/                       # 自动生成的校验副本
    ├── prefixes/                                # 自动生成的 Proton 状态
    └── executions/                              # 自动生成的任务记录
```

本项目自行构建的 helper 位于 checkout 的 `sidecar/build/`；版本化安装包位于
`sidecar/bin/<platform>/<version>/`，它们不是外部 DLL。完整注释见
[仓库与数据目录树](docs/PROJECT_LAYOUT.zh-CN.md)。

<a id="build-from-source"></a>

## 自行构建（Build from source）

这里只构建**我们的 helper**，不编译专有模型或外部 Worker。
已有 **Zig（加入 PATH）和 Bash** 时，在节点仓库根目录执行：

```sh
bash sidecar/build_relay.sh
```

生成 `sidecar/build/dlss-native-relay.exe` 及传输测试用 mock。
使用 **DIS 光流只需构建 relay**；这一步不需要 CUDA 头文件、NGX SDK、ReShade，
也不要求 Windows/MSVC 构建主机。未安装 helper 清单时，`@bundled/relay`
也能找到这个开发构建输出。mock 不执行 NR。

使用 **NVIDIA 光流**才另需 CUDA 头文件（cuda.h 及其包含文件），构建宿主原生 helper。
下例 python 指构建用解释器：

```sh
python sidecar/build_nvof.py --target linux-x86_64 --cuda-include /path/to/cuda/include
```

Windows 目标改为 `--target windows-x86_64`。输出为 `sidecar/build/dlss-nvof-helper`
或 `.exe`，不使用 nvcc；目标平台指 **Comfy 后端宿主**，不是浏览器电脑。

详细说明包含[工具链准备、本地使用及版本化 ZIP 打包](docs/distribution.zh-CN.md#维护者构建)，
包括 Windows 的 Bash 环境、校验和及安装位置；打标准包需同时备齐对应平台的两个 helper。
另见[开发与测试](docs/DEVELOPMENT.zh-CN.md)、[C++ 质量要求](docs/sidecar-cpp-quality.zh-CN.md)。
交叉编译成功不等于 Windows GPU 实机验收。

独立实验 SR 路线需要已有 Windows MSVC 工具链与官方 SDK，构建同一个 Worker；
NR 的 Zig 构建不链接 SR。[SR 构建命令与文件要求](docs/super-resolution.zh-CN.md#运行时文件与构建)
也说明了手动 Windows 构建工作流及尚未完成的 GPU 验收边界。

## 示例工作流

下载 JSON（GitHub 中选择 **Raw / Download raw file**），拖入 ComfyUI 或使用“打开”导入。
示例只需要 Comfy 原生视频节点及本项目，不依赖第三方视频加载节点包。

| 示例 | 用途 |
| --- | --- |
| [DIS 预览](example_workflows/dlss_native_preview.json) | 建议从这里开始：独立 DIS 提供器、输入检查、NR Look 与节点内预览。 |
| [完整视频导出](example_workflows/dlss_native_process.json) | 原尺寸处理完整输入，连接原生 Save Video。 |
| [NVIDIA 光流预览](example_workflows/dlss_nvidia_flow_preview.json) | 替换光流提供器，需要原生 NVOF helper。 |
| [两套 Look 对比](example_workflows/dlss_compare_looks.json) | A/B 分别连接一套 NR Look，共用输入准备缓存。 |
| [实验性 NR 处理链](example_workflows/dlss_pipeline_preview.json) | 独立输入组装、DIS/NVIDIA 惰性选择和串行 NR 阶段，见[处理链说明](docs/media-pipeline.zh-CN.md)。 |
| [外部运动处理链](example_workflows/dlss_external_motion_preview.json) | 可选数值运动引导导入；提供真实清单前默认使用已配置 DIS。 |
| [实验性 SR/DLAA](example_workflows/dlss_sr_experimental.json) | 需要真实设备深度清单与启用 SDK 的 `owned_sr` Worker 的接线模板，不是即导即用或已通过 GPU 验收的示例。 |
| [Streamline 重建](example_workflows/dlss_reconstruction.json) | 显式渲染器数据包 + `owned_sl`／CXR1 + 原生 SaveVideo；SR／DLAA／RR／RR+DLAA 已通过有限合成场景 GPU/Comfy 验收，须自备完整数据包和运行时预设。 |
| [VIDEO 接入 Streamline](example_workflows/dlss_streamline_video.json) | 统一输入 + 显式相机／深度 → Streamline SR/DLAA 阶段 → 处理链输出 → 保存视频；须提供匹配清单和 owned_sl 运行时。 |

[示例说明](example_workflows/README.zh-CN.md)介绍接线、默认值与操作顺序。
仓库不包含素材或专有 DLL；请将 `example-input.mp4` 换成自己的视频。
导入不等于保存到 Comfy 工作流库，如需保留请自行保存。
原 `workflows/` 目录保留旧模板，仅用于兼容已有引用。

## 中英文界面

在 ComfyUI 语言设置中选择 **English** 或 **简体中文**：
节点字段由官方 `locales/en,zh/nodeDefs.json` 机制翻译，自绘卡片跟随同一设置。
自绘区域遇到其他语言回退英文，中文地区变体使用简体中文。
首次增加翻译文件后需要重启后端，再刷新页面。

节点 ID、实际参数值、路径、哈希、复制出的执行 JSON 和未识别的底层错误保留原文，
不会因切换语言而改变渲染、缓存或排错信息。详见[翻译机制与覆盖范围](docs/i18n.zh-CN.md)。

## 更多文档

- [安装、平台与 helper 分发](docs/distribution.zh-CN.md)
- [外部 Worker 与 DLL 准备](docs/DLL_PREPARATION.zh-CN.md)
- [节点说明及输出范围](docs/comfy-video-nodes.zh-CN.md)
- [NR Look 参数](docs/nr-look.zh-CN.md)
- [多层 NR 轮次](docs/MULTI_PASS_NR.zh-CN.md)
- [FFmpeg 依赖与路径](docs/media-tools.zh-CN.md)
- [输入适配与色彩](docs/video-input-adapter.zh-CN.md)
- [NVIDIA 光流](docs/nvidia-optical-flow.zh-CN.md)
- [实验性 SR/DLAA 输入、运行时与构建](docs/super-resolution.zh-CN.md)
- [性能、常驻与监控](docs/preview-performance.zh-CN.md)
- [存储上限、流式处理与文件清理](docs/STORAGE.zh-CN.md)
- [架构设计](docs/ARCHITECTURE.zh-CN.md) · [运行时角色与薄 shim 原则](docs/RUNTIME_ROLES.zh-CN.md) · [直接 NR 协议](docs/direct-nr-relay.zh-CN.md)
- [项目与运行时目录结构](docs/PROJECT_LAYOUT.zh-CN.md)
- [开发说明](docs/DEVELOPMENT.zh-CN.md) · [完整文档索引](docs/README.zh-CN.md)

## 许可与安全

原创代码采用 [MIT](LICENSE)，附带 NVIDIA API 头文件保留原声明，详见
[第三方边界](THIRD_PARTY_NOTICES.md)。不分发专有 Worker/模型、驱动、Proton 或测试视频。

外部二进制会执行原生代码，独立进程/Proton prefix **不是安全沙箱**。
只使用可信文件并遵守来源许可；公开分享任务详情前请删除私有路径、主机名和素材名。
