# 外部运行时文件：身份、来源与放置位置

[English](DLL_PREPARATION.en.md) · 简体中文

Audience: public

**先 clone 节点，再准备依赖；不需要安装整个 Video Converter。**
当前 direct NR 的应用侧最小集合是：本项目 relay + 外部视频 Worker + 配套 NR 模型。
使用 NVIDIA 光流时另需本项目宿主原生 NVOF helper。驱动/Proton 的系统依赖不因此消失。
完整顺序见[安装与目录布局](distribution.zh-CN.md)。

## 一眼看懂：已经收集的文件最终是否使用

下表只针对**当前 active `direct_nr` 视频后端**，不表示其他文件在原项目里没有用途。

| 已收集文件 | 当前使用？ | 结论 |
| --- | --- | --- |
| `DLSS5VideoConverter/bin/runtime/` 中的 `nvngx.dll` | **是，必需** | 外部 D5V2 视频 Worker；作为 PE 程序启动，不导入 Python |
| 同一 converter runtime 目录中的 `nvngx_dlssnr.dll` | **是，必需** | 由该 Worker 加载的匹配 NR 模型/运行时 |
| 随 RenoDX/ReShade 文件取得的另一个同名 `nvngx_dlssnr.dll` | **否，除非另行完成配对验证** | 同名不代表 ABI、补丁级别和目标显卡匹配 |
| ReShade 包的 `dxgi.dll` / 解压得到的 `ReShade64.dll` | **否** | 保留 ReShade 路线的图形代理/hook；direct_nr 没有要注入的应用 |
| `renodx-dlss5.addon64` 或类似名称 RenoDX add-on | **否** | ReShade add-on；当前 Worker 协议不会加载它 |
| `nvngx_dlss.dll` | **否** | 其他路线使用的 DLSS Super Resolution 组件；当前输出仍是同分辨率 NR |
| `D3DCompiler_47.dll` | **否** | 只与特定 ReShade/Wine shader compiler 环境相关 |
| `sl.*.dll`、`nvngx_dlssg.dll` | **否** | Streamline/FG 组件；这些后端尚未实现 |

两个必需外部文件必须来自同一套已知兼容包，不能仅按文件名挑选。新增的 Setup Helper
会检查存在性、PE 身份与 Runtime Configuration 记录的哈希，并识别下文已测精确组合；
但陌生组合是否真正兼容仍须 GPU 执行验证，静态检查不能证明。

### 三种完全不同的 `nvngx.dll`

| 身份 | 常见位置 | 当前后端是否使用 |
| --- | --- | --- |
| **视频 Worker 可执行程序** | 用户数据 `components/nr/<bundle>/nvngx.dll`；已测文件为 67,072 字节 | **是。** Relay 以 `--video` 启动；我们的预设中 `worker` 就指它。 |
| **NVIDIA 驱动 NGX bootstrap** | DriverStore 或 Proton prefix 的 `system32/nvngx.dll`；尺寸/版本由驱动环境管理 | **只作为间接驱动环境。** 不复制覆盖视频 Worker，也不加入预设。 |
| **调用者验证 shim** | Zonnery 一类项目在播放器旁的 `caller/nvngx.dll` | **否。** 它是为该播放器服务的薄转发 DLL，不实现我们的 D5V2 进程协议。 |

与 Worker 配套的 NR 文件叫 `nvngx_dlssnr.dll`，不是 `nvngx.dll` 的第四种身份。
两种社区封装的二进制/导出证据以及未来“明确命名 Worker + 独立薄 shim”原则见
[NGX、Worker 与 shim 的角色边界](RUNTIME_ROLES.zh-CN.md)。

## 1. 当前实际使用的文件

| 文件 | 实际身份与作用 | 来源 / 获取线索 | 我们如何使用 |
| --- | --- | --- | --- |
| `dlss-native-relay.exe` | 本项目 C++ 传输与进程桥接程序，不是 DLSS 实现 | 本仓库 `sidecar/src/native_relay.cpp`，用 Zig 构建；将通过本项目 helper Release 提供 | Windows 原生启动，Linux 经 Proton；建立管道并启动外部 Worker |
| `nvngx.dll` | 虽然扩展名为 DLL，实际是 **Windows x64 控制台 PE 可执行程序**；接收 D5V2 视频流、执行 NR | 已测试文件来自下述 Video Converter RTX40 包的 `bin/runtime/`。上游将它归为改编自 DLSS5-Feeder host 的独立 Worker | Relay 启动 `nvngx.dll --video`；本项目当前不编译它，也不另外加载 caller shim |
| `nvngx_dlssnr.dll` | Worker 加载的 DLSS Neural Rendering 模型/运行时；不是普通 SR 库 | 已测试文件来自**同一个 RTX40 包**，是社区修改版，不应描述成我们从官方 SDK 获取的原版 | 放在 Worker 配套目录；任务建立独立快照后加载 |
| `dlss-nvof-helper` / `dlss-nvof-helper.exe` | 本项目 C++ NVIDIA Optical Flow API 适配程序，仅选择 NVIDIA 光流时需要 | 本仓库 `sidecar/src/nvof_helper.cpp`；Linux/Windows 分别构建 | 宿主原生运行，不走 Proton；不是 NR Worker 或模型 |

上游的[组件来源说明](https://github.com/perseval-BLR/DLSS5-Video-Converter/blob/main/others/THIRD_PARTY.md)
将 Worker 的来源指向 DLSS5-Feeder host。我们核实的是**获取到的字节和视频协议**，
尚未证明某次上游源码构建能逐字节复现该 Worker。不要把它标为本项目自研二进制。

### NVIDIA 光流使用的驱动库

以下是驱动接口，不是要另行复制进 NR 组件目录的文件：

| 宿主 | 本项目 NVOF helper 加载的库 | 来源 / 位置 |
| --- | --- | --- |
| Windows | `nvcuda.dll`、`nvofapi64.dll` | NVIDIA 驱动安装；helper 从系统目录加载 |
| Linux | `libcuda.so.1`、`libnvidia-opticalflow.so.1` | NVIDIA 驱动软件包，由宿主动态链接器的库路径解析 |

缺失时检查宿主 NVIDIA 驱动组件，不从随机站点找同名 DLL，也不把 Windows 库复制给
Linux helper。CUDA/Optical Flow 开发头文件用于构建；使用我们预编译 helper 的用户，
不需要仅为运行该 helper 安装编译器或 CUDA Toolkit。

## 2. 我们已测试的确切来源

2026-09-03 从已有本地归档重新核对；未重新下载或执行第三方程序：

- 项目：[perseval-BLR/DLSS5-Video-Converter](https://github.com/perseval-BLR/DLSS5-Video-Converter)。
- [v0.1.0 发布页](https://github.com/perseval-BLR/DLSS5-Video-Converter/releases/tag/v0.1.0)。
- 归档名：`DLSS5-Video-Converter-v0.1.0-RTX40-win64.zip`。
- 取用的两个成员：

  ```text
  DLSS5VideoConverter/bin/runtime/nvngx.dll
  DLSS5VideoConverter/bin/runtime/nvngx_dlssnr.dll
  ```

| 文件 | 字节数 | 本地已测文件 SHA-256 |
| --- | ---: | --- |
| nvngx.dll | 67,072 | `99ef1f2976d9cd16b7fc269adb6c6450fb64c81a522c9b9e6edc6a28201dc904` |
| nvngx_dlssnr.dll | 165,840,496 | `28bdc080d28686decdb63f6f4246b022274916b80aafdab266fe0fb63b2b9265` |

文件名、尺寸和哈希用于识别**测试组合**，不是安全背书、许可证明或最低版本要求。
上游主分支 THIRD_PARTY.md 列出的模型哈希是 `DCC0DC…D36F`，与此 RTX40 包不同；
不要用主分支的一张表替代具体 Release 资产校验。

RTX40 发布说明将模型称为 Ada 社区补丁版，并提到 Uncle Burrito / dev-camo。
这是**发布者的归属说明**，不等于我们独立验证了补丁作者、原始模型来源或完整修改链。
原始厂商来源与可再分发权仍须向分发者核对；这里不提供 DLL 镜像或自动下载。

### 显卡系列怎么选？

| 系列 | 当前可确定的结论 |
| --- | --- |
| RTX 40 | 上述精确组合已在 RTX 4070 Ti SUPER + Linux/Proton 上通过 NR 测试；不是整代显卡保证 |
| RTX 50 | 参考项目有面向 RTX50 的版本线索；需重新确认其 Worker 是否实现当前 D5V2，并做独立验收，不能自动用 RTX40 补丁包 |
| RTX 20/30 | 本项目未验证。上游概览与 RTX40 资产说明存在不同限制，不能据概览中的“beta”声称本包支持 |

同一文件名不代表 ABI、协议或 GPU 支持相同。跨版本配对、常驻复用和 Windows GPU
均有独立验证要求；未验证组合先使用隔离模式。详见[运行时边界](dlss5-runtime-research-2026-09.zh-CN.md)。

## 3. 为什么别的项目还要求其他 nvngx 文件？

| 名称 | 在参考项目中的身份 | 当前 direct NR 要手动补吗？ |
| --- | --- | --- |
| 驱动的 `nvngx.dll` / 重命名的 `_nvngx.dll` | NVIDIA NGX 核心 bootstrap 库；Zonnery 列为其路线的依赖 | 不作为我们的三个应用组件之一；不能拿它替代视频 Worker。仍需正确安装驱动 |
| `caller/nvngx.dll` | Zonnery 的调用者验证薄转发 DLL，由播放器加载 | 不需要额外准备；它不能充当 D5V2 视频可执行程序 |
| `nvngx_dlss.dll` | DLSS Super Resolution 运行库 | NR 不需要；独立实验性 [SDK SR 路径](super-resolution.zh-CN.md)需要它和 SDK Worker，单独复制该 DLL 不会启用 SR |
| `nvngx_dlssg.dll`、`sl.*.dll` | Frame Generation / Streamline 相关组件 | 当前不需要，FG/Streamline 未接入；[独立 SDK SR 路径](super-resolution.zh-CN.md)使用 `nvngx_dlss.dll` 和 SDK Worker |

[Zonnery 的依赖表](https://github.com/Zonnery/dlss5-nr-player)
描述的是它自己的播放器，不是可直接照搬到这里的采购清单。尤其不能把该表关于
“驱动附带 NR 模型”的说法视为所有驱动版本的保证。

[NR-Media-UI](https://github.com/perseval-BLR/NR-Media-UI) 是图片应用，提供了不同 GPU 包的线索，
不是已验证的本项目视频 Worker 替代品。
[DLSS-COM](https://github.com/MYT-YEP/DLSS-COM) 使用自己的 D3D12 Worker，要求用户另放模型；
因此它可能只要求用户补一个模型 DLL。这不表示我们的 relay 已经实现了它的 Worker。

### 两个参考项目的 README 清单如何映射到本项目

| 上游项目 | 它描述的文件/运行时 | 我们复用什么 |
| --- | --- | --- |
| [Zonnery/dlss5-nr-player](https://github.com/Zonnery/dlss5-nr-player) | 直接 NGX 播放器构建：重命名为 `_nvngx.dll` 的驱动核心、`nvngx_dlssnr.dll`、NGX 头文件、`caller/nvngx.dll`；其 DX11 bridge 另需 `nvngx_dlss.dll`；ffmpeg/ffprobe 为外部工具 | **不单独复用其 bootstrap/shim/头文件。** README 对直接 NGX 原理有参考价值，但当前选用的外部 Worker 自己承担调用契约。 |
| [perseval-BLR/DLSS5-Video-Converter](https://github.com/perseval-BLR/DLSS5-Video-Converter) | 完整 Windows 网页应用，带嵌入 Python/FFmpeg 和 `bin/runtime`；其 Release runtime 内含视频 Worker 与模型 | 对已测 RTX40 包，只取 `bin/runtime/nvngx.dll` 与配套 `nvngx_dlssnr.dll`。不复用网页服务、嵌入 Python、打包 ffmpeg、`nvidia-smi.exe`、任务/输出目录或 launcher。 |

两个项目封装的是不同调用架构；它们的文件清单不能相加，同名文件也不能默认互换。

## 4. 外部文件到底放哪里？

推荐放在**节点源码之外、Comfy 用户数据之内**，每套版本独立：

```text
ComfyUI/
  custom_nodes/comfy-dlss-experimental/        # git clone 的源码
  user/default/comfy-dlss-experimental/       # 默认数据根
    components/nr/converter-v0.1.0-rtx40/
      nvngx.dll
      nvngx_dlssnr.dll
    runtime-presets/default.json
```

完整的源码、用户数据和自动生成目录说明见[项目目录](PROJECT_LAYOUT.zh-CN.md)。

自定义 Comfy user 目录或 COMFY_DLSS_HOME 会改变数据根；以实际配置为准。
不放 System32、DriverStore、Python site-packages、Proton 系统目录，也不覆盖驱动文件。
不需要运行 DLSS5VideoConverter.exe 或携带其嵌入 Python/网页服务。

复制[预设模板](../examples/runtime-presets/direct-nr.example.json)到上述 default.json：

```json
{
  "schema_version": 1,
  "id": "direct-nr-local",
  "backend": "direct_nr",
  "components": {
    "relay": "@bundled/relay",
    "worker": "../components/nr/converter-v0.1.0-rtx40/nvngx.dll",
    "nvngx_dlssnr": "../components/nr/converter-v0.1.0-rtx40/nvngx_dlssnr.dll"
  }
}
```

相对路径以 **default.json 所在目录**为基准，不是 Comfy 根或当前 shell。
也可使用服务器本机的绝对路径，Windows JSON 中可用正斜杠避免反斜杠转义。
这只是固定位置的示例，不会自动下载/导入文件，也不修改已有用户预设。

运行时按字节哈希创建 runtime-snapshots 副本；不要手改快照，修改源组件/预设。
多个版本建立多个预设；换 DLL 需新实例，不在已加载进程中热替换。

## 5. 旧 ReShade/RenoDX 诊断文件

以下用于保留的研究路线，**不要放进当前 direct_nr 预设**：

| 文件 | 来源及作用 |
| --- | --- |
| ReShade 的 `ReShade64.dll` → 应用目录 `dxgi.dll` | 来自 [ReShade](https://reshade.me/) 的 x64、支持 add-on 的发行版；代理加载器/图形 hook，不是系统 DXGI |
| `renodx-dlss5*.addon64` | 此前取得于 RenoDX 社区频道的实验 add-on；与 ReShade 配合，精确发布者/版本/获取链接需另核对，不能用普通 RenoDX 包替代 |
| `nvngx_dlss.dll` / `nvngx_dlssnr.dll` | 该诊断对应的 SR/NR 组件；旧 Discord 文件不默认等于已验证 RTX40 视频包 |
| `D3DCompiler_47.dll` | Microsoft shader compiler；仅特定 Wine 编译兼容问题需要原生版本；按可信 Microsoft 发行来源核对，不使用随机 DLL 下载站 |

该路线可使用 `d3dcompiler_47=n;dxgi=n,b` 子进程覆盖（native / builtin），
但不会安装 DLL、解决 Worker 协议或用于当前 direct NR。
[旧组件清单](../examples/component-manifests/renodx-dlss5.example.json)不包含运行时文件。

## 6. 获取途径还需要确认什么？

逐个资产确认：原发布者、版本/发布日期、目标 GPU、确切下载项、包内路径、哈希、
使用与分发条款。项目主页和 README 只能作为线索，不是下载权限或兼容证明。
本仓库不镜像/补丁/自动获取这些外部 NR 二进制。外部程序会执行原生代码；
子进程和 Proton prefix **不是安全沙箱**。
