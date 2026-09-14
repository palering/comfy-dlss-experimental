# NGX、Worker 与 shim 的角色边界

[English](RUNTIME_ROLES.en.md) · 简体中文

Audience: public

本页解决一个最容易误判的问题：不同项目把三种完全不同的程序都命名为
`nvngx.dll`。文件名不能表示身份，必须同时检查 PE 类型、入口/导出、启动方式和协议。

## 已确认的两种社区封装

| 项目文件 | 二进制/调用身份 | 实际职责 | 能否互换 |
| --- | --- | --- | --- |
| Video Converter `bin/runtime/nvngx.dll` | Windows x64 控制台 PE，有程序入口、无导出目录；以 `nvngx.dll --video` 启动 | 完整视频 Worker：实现 D5V2 帧协议并宿主 Feature 18 生命周期 | 否 |
| Zonnery `caller/nvngx.dll` | 被播放器 `LoadLibrary`，契约要求导出 `DLSSNR_CallInit/Create/Evaluate/Release` | 极薄的进程内函数转发层，使实际 NR 调用来自符合运行时检查的模块名 | 否 |

Zonnery 仓库没有分发该 shim 二进制或源码；以上 shim 契约来自其播放器源码中的
`LoadLibrary/GetProcAddress` 和 README。Video Converter 文件则已直接检查 PE 头：它是控制台
可执行程序且没有导出表。把前者放进我们的 Worker 位置不会实现 D5V2；把后者放进
Zonnery 的 `caller/` 目录也无法解析四个导出函数。

Video Converter Worker 同时以 `nvngx.dll` 命名，很可能让完整宿主自身满足相同的调用者
身份条件；这是上游说明与行为支持的高可信结论，但没有可审查的对应 Worker 源码。
因此文档只确认它的二进制身份、D5V2 行为和外部依赖，不声称能逐行还原内部实现。

第三种同名文件是 NVIDIA 驱动的 **NGX bootstrap/core `nvngx.dll`**。它由驱动环境管理，
不是上述视频 Worker，也不是项目 caller shim，不能复制覆盖任意一方。

## 保留的旧实现（direct_nr）

```text
ComfyUI Python
  → dlss-native-relay.exe             本项目：进程/管道中继
  → nvngx.dll --video                 外部 Video Converter Worker
  → nvngx_dlssnr.dll                  与 Worker 匹配的 NR 运行库
```

这条旧后端不使用我们的自有 Worker/caller。
`dlss-native-relay.exe` 只监管进程和搬运 D5V2 字节，不创建 D3D12 设备或 NGX Feature。

## 自有后端（owned_nr）

独立的[自有运行时](owned-runtime.zh-CN.md)已实现下面的实验 NR 路径。
带相机数据的 Streamline 路径使用下述独立 `owned_sl` 后端与 Worker，
并不是 NR caller 的替代品。

```text
ComfyUI Python
  → 本项目传输/进程层
  → comfy-dlss-worker.exe             本项目拥有的明确 Feature Host
      → caller/nvngx.dll              可选、极薄、项目拥有、可审查
          → nvngx_dlssnr.dll          用户提供的 NR 功能运行库
      → NGX core / Streamline         由所选后端显式管理
```

重构遵循以下原则：

1. 完整 Worker 使用清楚的项目名称，不再伪装成普通 `nvngx.dll`。
2. caller shim 只保留有类型的 Init/Create/Evaluate/Release 转发；不拥有视频协议、
   D3D12 资源、缓存、线程、模型选择或 Worker 生命周期。
3. 只有实际运行库需要调用者验证时才启用 shim；它不是所有 NGX 后端的固定依赖。
4. Worker 负责设备、资源、同步、能力协商和错误边界；NR/SR/FG 分别作为可声明能力的
   后端，不通过收集同名 DLL 自动启用。
5. NVIDIA/社区修改的功能运行库由用户提供并按哈希、版本、目标 GPU 和已验证 Worker
   组合绑定；项目不把未知同名文件视为兼容。
6. 重构期间保留当前 D5V2 外部 Worker 后端，新的自研后端使用新的 ID/协议版本，
   不静默改变旧预设含义。

薄 shim 能降低调用者验证变化对完整 Worker 的影响，但它本身不能让 NR 工作。真正的
工程量仍在 D3D12/NGX 资源契约、输入补全、Feature 生命周期与跨平台进程边界。

## Streamline 重建后端（owned_sl）

[重建节点](reconstruction-nodes.zh-CN.md)使用显式渲染器数据包与 CXR1，
不是旧 `owned_sr`／CSR1 或 NR 协议：

```text
渲染器数据包 → 独立 Python 执行层 → comfy-dlss-sl-worker.exe
  → sl.interposer.dll + sl.common.dll + NvLowLatencyVk.dll
  → sl.dlss.dll + nvngx_dlss.dll          SR / DLAA
  → sl.dlss_d.dll + nvngx_dlssd.dll       光线重建 / RR + DLAA
  → 编码 VIDEO → ComfyUI SaveVideo
```

`owned_sl` 预设精确绑定上述八个文件与显式项目 UUID。任务使用哈希校验快照和
隔离 Worker，不使用 NR 的 `nvngx.dll` caller shim、常驻 Worker、FG 或 MFG。
SR／DLAA／RR／RR+DLAA 已通过有限合成场景的 Linux/Proton GPU 与 Comfy 执行验收；
这不等于原生 Windows、普通视频重建或长视频画质验证。

## 功能 DLL 不是宿主程序

| 文件族 | 主要角色 | 需要额外实现的部分 |
| --- | --- | --- |
| `nvngx_dlssnr.dll` | 当前实验 NR / Feature 18 运行库 | NR Worker、颜色/运动等输入契约 |
| `nvngx_dlss.dll` | Super Resolution / DLAA | SR 输入/输出尺寸、抖动、运动、曝光、资源状态 |
| `nvngx_dlssd.dll` | 官方 Ray Reconstruction 路线 | 光照/深度/运动等所需引擎语义 |
| `nvngx_dlssg.dll` | Frame Generation | 帧呈现、深度、运动、HUD/UI、同步与平台能力 |
| `sl.*.dll` | Streamline 框架/插件 | Streamline 初始化、资源标记、功能常量和生命周期 |

这些文件不是命令行视频工具。放进目录只满足某个后端的一个依赖，不会自动产生 SR、FG
或 NR 功能。当前精确文件准备见[外部运行时文件](DLL_PREPARATION.zh-CN.md)，整体数据流见
[架构](ARCHITECTURE.zh-CN.md)。
