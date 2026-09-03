# comfy-dlss-experimental

[English](README.en.md) · 简体中文

Audience: public

用于研究**离线视频神经渲染（NR）**的 ComfyUI 自定义节点，提供节点内 A/B 预览与独立正式导出流程。

> **目前是源码预览版。** clone 只安装节点源码，不代表 NR 已能运行。
> 还需本项目 helper，以及可信、相互匹配的外部 Worker 与模型 DLL。
> **目前尚未发布预编译 helper Release 包**。Linux/Proton 已进行 GPU 实测；
> Windows 代码和 CI 已有，但 Windows GPU 实机验收仍待完成。

[快速开始](#quick-start) · [前置条件](#前置条件) ·
[自行构建](#build-from-source) · [示例工作流](#示例工作流)

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
- 可连接的 **DIS CPU 光流**与可选 **NVIDIA 硬件光流**。
- 视频信息检查、显式解释缺失色彩标签、可选 SDR → sRGB 工作空间转换。
- 独立缓存颜色/光流准备结果；改 Look 不重复准备相同输入。
- 按任务释放或懒加载常驻 Worker，查看耗时、资源、记录并手动释放。
- 输出标准 **VIDEO**，连接 ComfyUI 原生 **保存视频 / Save Video**。

**尚未实现：**SR 超分、FG 补帧、HDR 处理、外部深度/材质/蒙版输入、
原生 Linux NR、对比分屏视频导出。部分实验参数可能在某些 Worker/模型上无可见响应。

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

## 示例工作流

下载 JSON（GitHub 中选择 **Raw / Download raw file**），拖入 ComfyUI 或使用“打开”导入。
示例只需要 Comfy 原生视频节点及本项目，不依赖第三方视频加载节点包。

| 示例 | 用途 |
| --- | --- |
| [DIS 预览](example_workflows/dlss_native_preview.json) | 建议从这里开始：独立 DIS 提供器、输入检查、NR Look 与节点内预览。 |
| [完整视频导出](example_workflows/dlss_native_process.json) | 原尺寸处理完整输入，连接原生 Save Video。 |
| [NVIDIA 光流预览](example_workflows/dlss_nvidia_flow_preview.json) | 替换光流提供器，需要原生 NVOF helper。 |
| [两套 Look 对比](example_workflows/dlss_compare_looks.json) | A/B 分别连接一套 NR Look，共用输入准备缓存。 |

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
- [FFmpeg 依赖与路径](docs/media-tools.zh-CN.md)
- [输入适配与色彩](docs/video-input-adapter.zh-CN.md)
- [NVIDIA 光流](docs/nvidia-optical-flow.zh-CN.md)
- [性能、常驻与监控](docs/preview-performance.zh-CN.md)
- [架构设计](docs/ARCHITECTURE.zh-CN.md) · [直接 NR 协议](docs/direct-nr-relay.zh-CN.md)
- [开发说明](docs/DEVELOPMENT.zh-CN.md) · [完整文档索引](docs/README.zh-CN.md)

## 许可与安全

原创代码采用 [MIT](LICENSE)，附带 NVIDIA API 头文件保留原声明，详见
[第三方边界](THIRD_PARTY_NOTICES.md)。不分发专有 Worker/模型、驱动、Proton 或测试视频。

外部二进制会执行原生代码，独立进程/Proton prefix **不是安全沙箱**。
只使用可信文件并遵守来源许可；公开分享任务详情前请删除私有路径、主机名和素材名。
