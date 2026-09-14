# 自有 NR 流式接口（开发用途）

[English](owned-protocol.en.md) · 简体中文

Audience: public

CNR1 已实现，可通过 [owned_nr](owned-runtime.zh-CN.md) 显式选择。它与旧 D5V2、诊断
JSON 和 CNS1 测试文件格式分开。`owned_cnr1_nr` 契约不代表自动安装或回退。

## 组件

| 组件 | 职责 |
| --- | --- |
| `sidecar/src/nr_engine.*` | 模型、Feature、纹理、传输和 GPU fence。 |
| `sidecar/src/nr_caller.*` | 无状态薄转发，不管理视频或进程。 |
| `sidecar/src/owned_wire.hpp` | 有上限的二进制布局和设置校验。 |
| `sidecar/src/worker_connection.hpp` | 带认证和截止时间的本机传输。 |
| `sidecar/src/nr_probe.cpp` | 诊断入口与 `--serve-nr` 会话循环。 |
| `comfy_dlss_experimental/owned_worker.py` | 有序 Python 协议客户端。 |
| `comfy_dlss_experimental/owned_process.py` | 分平台进程管理、有大小上限的启动日志排空、清理。 |

Windows 直接执行 PE；只有 Linux 才加载 Proton/prefix/固定进程身份清理逻辑，使用
`proton run`，不需要 relay PE 或 systemd。Windows GPU 尚未验收；macOS 仅编辑／构建。

## 协议与生命周期

Worker 连接宿主 IPv4 本机监听地址，发送 32 字节随机令牌，等待单字节确认。
32 字节小端头包含 uint32 魔数 `CNR1`、版本 1、类型、负载长度，随后是 uint64
请求／会话 ID。请求从 1 连续递增，响应回显 ID。格式错误、超限、未知或状态不符
的请求失败后关闭连接，不复用不确定会话。

| 请求／响应 | 效果 |
| --- | --- |
| CAPS (128) | 初始协议能力，不代表 GPU 画质验收。 |
| CREATE (1) / ACK (129) | 按尺寸和 Look 参数创建一个会话。 |
| FRAME (2) / RESULT (130) | 一帧输入对应一帧有限 FP16 输出，等待 GPU fence 后返回，保留 PTS。 |
| END (3) / ACK | 结束有输出的任务，保留分配；新任务必须重置，PTS 可重新计时。 |
| RELEASE (4) / ACK | 释放会话／GPU 对象，进程可再次 CREATE。 |
| SHUTDOWN (5) / ACK | 释放并退出。 |
| PING (6) / ACK | 检查连接，不创建 GPU 会话。 |
| ERROR (131) | 结构化阶段／错误码／帧诊断，随后关闭，不复用不确定会话。 |

CREATE：八个 uint32（宽、高、预热、preset、style、自动蒙版、UI 修正、保留零），
四个 float32 强度（总体、色调、结构、皮肤）。更改 Look 当前需要 RELEASE/CREATE，
不提供 DLL 热切换。FRAME：uint64 非负纳秒 PTS、uint32 reset／保留零，随后是
紧密排列的 RGBA16F 颜色和 RG16F 运动，运动为当前到前一帧、全分辨率像素单位。
RESULT：相同 uint64 PTS 和 RGBA16F 颜色。不隐式转换传递函数、生成深度、缩放或
补帧。输出可超出 SDR [0,1]，显示／编码策略由调用方负责；旁路与 A/B 混合也在 NR 外。

一个会话、一个在途帧。参数范围：64–1920 × 64–1080，最大负载 24,883,216 字节，
不代表范围内尺寸均已 GPU 验收。流式 Worker 不写全片中间文件。预热只在会话首张
正式帧前执行；正式首帧仍遵守 reset。END 后下一任务不重复预热。宿主空闲策略为
30–900 秒，传输提供 960 秒空闲宽限。活动看门狗 120 秒，GPU fence 30 秒，网络 I/O 也有截止时间。断开取消
传输，不能抢占已提交内核；GPU 完成不确定时直接终止，不析构在途资源，再由宿主
清理自己的进程。节点自动空闲调度和手动释放已接通。

`OwnedWorkerError` 包含 `stage`、`code`、`code_domain`、`frame_index`、
`evaluation_index` 和可读消息。NGX／HRESULT 错误码以十六进制显示，区分初始化、
Feature 创建、推理和传输失败，不必从一个通用错误猜原因。CNR1 成功消息格式不变。
宿主也兼容旧的四字节错误负载；旧宿主收到新错误时会安全失败，但详情较少。
排查时应使用配套版本的宿主代码和 helper。

## CSR1：可选 SDK SR/DLAA 传输

CSR1 在同一个带鉴权的回环传输上使用独立小端协议，不修改或重新解释 CNR1。
同一个 Worker 用 `--serve-sr MODEL_DIRECTORY LOG_DIRECTORY PROJECT_UUID PORT TOKEN`
选择此模式；公共 SDK 加载 SR，不经过 NR caller。见 [SR 配置](super-resolution.zh-CN.md)。

- 包头 32 字节：四个 uint32 为 magic=0x31525343、version=1、type、payload_bytes，
  后接 uint64 request_id、session_id。消息编号沿用 CNR1。
- CAPS：16 个 uint32，描述输入最小/最大尺寸、输出上限、颜色/运动/深度/输出格式 ID、
  包大小/会话/在途数量限制、SR/DLAA 位、SDK 编译标志与累计帧上限。
  编译标志为零时在创建设备前失败；为一也不代表 GPU/模型验收通过。
- CREATE：8 个 uint32，依次为输入宽高、输出宽高、质量、preset、flags、保留零。
  flags 第 0–3 位分别是 HDR、反向深度、含 jitter 的运动、自动曝光；质量与 preset 沿用 SDK 值。
- FRAME：uint64 pts_ns、uint32 reset/reserved_zero，以及 8 个 float32：
  jitter X/Y、运动缩放 X/Y、曝光、pre-exposure、曝光缩放、帧间隔毫秒。
  48 字节元数据之后接输入尺寸的 RGBA16F 颜色、RG16F 运动、R32F 设备深度。
- RESULT：相同 uint64 PTS 加**输出尺寸**的 RGBA16F；输入/输出尺寸可不同，仍是一帧进一帧出，不是 FG。
- END 在低层保留会话，RELEASE 释放功能，SHUTDOWN 退出。每个任务首帧必须 reset，
  任务内 PTS 严格递增。当前 Comfy SR 执行器选择 isolated，不复用会话。

限制为一个会话/一帧在途，输入最大 1920×1080、输出最大 3840×2160，
包最大 66,355,208 字节、每会话最多 1,000,000 次评估。CER1 错误沿用 CNR1 的有界
阶段/错误码/帧诊断。GPU fence 30 秒、活动 watchdog 120 秒；失败/取消只回收本任务进程，
不回退到普通缩放或 NR。协议源码与 CPU 测试已实现，SDK 链接和 GPU 输出另行验收。
默认 Zig 构建只有 SR 不可用握手，不含已链接的 SDK SR 引擎。

## 验证边界

Linux/GE-Proton、单台 RTX 4070 Ti SUPER 已通过：真实帧加 DIS、重复片段重置一致性、
两个任务复用会话、显式释放、更改 Look 后重建、错误帧拒绝和空闲断连清理。
流式输出与修正后的文件测试逐字节一致。测试使用 640×360 和一次丢弃预热，不代表
正式吞吐或长视频验收。旧 RGBA8 与新 FP16 画面目视接近，不宣称新旧逐字节一致。

Comfy 短片处理／保存、单帧预览及任务间复用已通过，可选辅助程序打包已实现。
长视频／资源增长测试、Windows GPU 验收及 SR/FG 仍是独立工作。
保留旧预设与 DLL，参见[构建／ABI 检查](owned-worker.zh-CN.md)。
