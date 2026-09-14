# Streamline 重建节点

[English](reconstruction-nodes.en.md) · 简体中文

Audience: public

## 已接通的范围

显式 `owned_sl` 路径连接 **重建数据包输入 → 重建渲染 → 保存视频**，通过独立 CXR1
Worker 执行 SR、DLAA、RR 或原分辨率 RR。运行库配置接入重建渲染节点，不改变现有
`direct_nr`、`owned_nr`、`owned_sr` 工作流。此链路不含 FG/MFG 或 NR 卡片内的 A/B 预览。

Linux/Proton 有界测试已通过四种模式的实际 Comfy 节点执行和 Save Video，以及带前置
历史的单帧输出。文件执行核心的原始结果与既有独立 GPU 探测逐字节一致。独立检查确认
了保存文件的帧数、从零开始的 30 fps 时序与 SDR 标记；另一个三帧文件核心音频测试
通过了对齐裁切、AAC 输出及历史帧不导出的验证。这些是合成
场景的集成测试，不代表自然视频画质、长时间稳定性、原生 Windows 或浏览器交互已验收。

普通 MP4 **不包含**所需的相机、设备深度及 RR 材质缓冲。必须提供渲染器导出的真实数据；
本路径不估计、不伪造缺失信息。清单声明和校验也不能证明输入平面确实对应原始渲染器。

## 首次使用

已有 VIDEO 并另行重建了相机／深度时，可通过[统一 VIDEO 输入路径](streamline-video-input.zh-CN.md)
执行 SR/DLAA。该入口共用执行器，但不要求包含原始颜色平面的渲染数据包。

1. 按 [Streamline 构建说明](streamline-reconstruction.zh-CN.md#构建与运行库)生成
   `comfy-dlss-sl-worker.exe`。
2. 从 [owned_sl 预设模板](../examples/runtime-presets/owned-sl.example.json)配置显式 Worker
   与用户提供的兼容七个 DLL。组件相对路径以预设文件目录为基准；项目不附带厂商文件。
3. 导入 [dlss_reconstruction.json](../example_workflows/dlss_reconstruction.json)，填写运行库
   预设路径，以及输入清单在 **Comfy 后端主机**上的绝对路径。关闭 Worker 常驻。
   Linux 要求已有 Proton 和 Xwayland，不要求前台窗口；macOS 不能执行该 GPU 后端。
4. 先保留 `frame_count=1` 检查单帧，再尝试短范围；`frame_count=0` 输出数据包剩余全部。
   `start_frame` 从零计数，超出数据包范围会报错，不静默截短。
5. `feature=sr, mode=dlaa` 是 DLAA；`feature=rr, mode=dlaa` 是原分辨率 RR。
   DLAA 忽略输出宽高；其他模式要求更大的偶数输出尺寸、严格相同宽高比，且通过运行库
   最优尺寸查询。

保存视频节点显示返回的标准 VIDEO。单帧结果是单帧视频，不是 NR 交互式 A/B 卡片。
`history_frames` 最多计算此前 120 帧历史，但不把它们导出；首个计算帧及源切镜标记会
重置时序历史。更改参数不会复用旧 GPU 实例；模型预设使用运行库默认值。

## 版本 1 渲染数据包

UTF-8 JSON 顶层必须且只能包含以下字段：

| 字段 | 值 |
| --- | --- |
| `schema_version`、`kind` | `1`、`"dlss_reconstruction_bundle"` |
| `width`、`height` | 整数，64×64 至 1920×1080；所有平面均为完整输入分辨率 |
| `fps` | 如 `"30/1"` 的有理数字符串；[1,120] 范围内恒定帧率 |
| `color_transfer` | `"srgb"` 或 `"linear_sdr"`；RR 必须为线性 SDR |
| `jitter_policy` | `"unjittered_video"` 或 `"external_render_metadata"` |
| `depth_inverted`、`has_rr` | 显式布尔值 |
| `frames` | 1–100000 个有序帧对象，同时受清单 32 MiB 上限约束 |
| `audio_source` | `null`，或与零点对齐、哈希绑定的本地音频／媒体文件 |

全部颜色使用 BT.709/sRGB 原色、全范围、小端原始数值平面。此版本不表示 HDR、其他
原色、缩放引导、裁剪、可变帧率、缺失时间戳或包含采样 jitter 的运动向量。

每帧必须且只能包含 `pts_ns`、`camera`、`metadata`、`planes`；RR 还需要
`world_to_view` 和 `view_to_world`。时间戳从零开始，须与
`round(index × 1000000000 / fps)` 相差不超过 1 纳秒；不能把 VFR 重新标成 CFR。

`camera` 包含完整 `CameraFrame` 字段：四个扁平、行主序、16 数字矩阵
`view_to_clip`、`clip_to_view`、`clip_to_previous`、`previous_to_clip`；三个数字组成的
`position`、`up`、`right`、`forward`；数值 `near_plane`、`far_plane`、`vertical_fov`
（弧度）、`aspect`，以及布尔 `orthographic`。矩阵对必须互逆，相机轴必须正交归一。
RR 的两个 world/view 矩阵也是扁平 16 数字数组组成的互逆矩阵对。

`metadata` 需包含全部八个 `SLFrameMetadata` 字段，即使数值不变也不可省略：
`jitter_x`、`jitter_y`、`motion_scale_x`、`motion_scale_y`、`pre_exposure`、
`exposure_scale`、`exposure`，以及布尔 `reset`。Jitter 是真实输入采样偏移，以输入像素
为单位，范围 ±0.5；无抖动输入必须为零。曝光值为正；SR 未使用的手动曝光必须为 1，
RR 会使用该曝光值。

| `planes` 角色 | 紧密格式及语义 |
| --- | --- |
| `color` | RGBA16F |
| `motion` | RG16F XY，当前到前帧的输入像素位移，包含相机运动 |
| `depth` | [0,1] 的 R32F 设备深度，与 `depth_inverted` 对应 |
| `diffuse_albedo`、`specular_albedo`（RR） | 线性 RGBA16F 反照率 |
| `normal_roughness`（RR） | RGBA16F，单位 XYZ 法线与线性粗糙度 |
| `specular_motion`（RR） | RG16F 反射运动 |

每个平面和可选 `audio_source` 都是仅含 `path`、小写 `sha256` 的对象，例如
`{"path":"frames/color-0000.rgba16f","sha256":"<64 位十六进制>"}`。路径使用相对 POSIX
分隔符，解析后必须是清单目录内的普通文件；拒绝绝对路径、父目录跳转和逃逸符号链接。
多帧可引用同一个未改变的平面文件。必需角色不可缺少，也不接受额外角色。输入节点检查
元数据和长度，渲染时逐帧验证像素哈希；Worker 在 GPU 评估前独立检查数值。

## 输出、音频与资源边界

RGBA16F 输出先检查有限值，裁切至 SDR，再量化为 RGBA8，编码为 H.264 YUV420。
线性 RGB 显式转换到 sRGB；alpha 不做伽马转换，视频编码也不保留透明度。报告包含
原始 FP16／导出像素哈希及越界裁切数量，不保存浮点／HDR 母版。

可选音频必须哈希绑定、从时间线零点开始，并覆盖选定范围。导出器从首个输出帧的时间
取音频，重新编码为 AAC，并将输出视频时间重置到零点。音频对齐由数据导出方保证；
本功能不估计口型同步，也不修复音频偏移。

运行库文件经 SHA-256 核验后复制为隔离快照；源文件或快照变化会报错，不覆盖修复。
每个任务逐帧输入和输出，复用既有磁盘配额及取消机制，只关闭自己持有的 Worker。
缺帧、评估失败和编码截断均为错误，不作为成功回退结果。复用现有 GPU 串行化和空闲
NR 释放机制，不启用多个功能实例并发运行。
