# VIDEO 与显式相机输入的 Streamline 链路

[English](streamline-video-input.en.md) · 简体中文

Audience: public

## 已接通的路径

`视频输入适配 → 输入组装器 → Streamline SR / DLAA 阶段 → 处理链输出 → 保存视频`。
数值设备深度和新增的**相机时间线输入**连接到同一个组装器；两个提供者须来自同一个
输入序列。现有 DIS／NVIDIA／外部数值／显式零运动配置继续可用。序列中枢可以分发该
序列，不复制或更改其 VIDEO 身份。

此路径使用 `owned_sl` / CXR1，与[渲染数据包入口](reconstruction-nodes.zh-CN.md)
共用 GPU／编码器生命周期；不会静默改走独立的 `owned_sr` / CSR1。NR 单独保留。
混合 NR/SL 阶段、RR 材质推断、FG 和 NR 交互式 A/B 卡片不在本 VIDEO 路径内。
RR 仍通过显式渲染数据包输入。

导入 [VIDEO Streamline 示例](../example_workflows/dlss_streamline_video.json)，填写后端
本地相机／深度清单路径和 `owned_sl` 预设，关闭 Worker 常驻。输入需归一化为 sRGB
SDR。处理链输出的 `scale` 保持 100；实际目标尺寸由阶段设置。DLAA 保留输入尺寸，
其他模式要求更大的偶数目标尺寸、严格相同宽高比，并通过运行库的尺寸查询。

阶段新增 `output_size_mode`：手动宽高、1.5×、2×、3×。倍率按本次实际输入的视频
尺寸计算，换视频后重新运行即重算，无需手动查询原尺寸。倍率模式隐藏并忽略手动
宽高，切回手动恢复原值；DLAA 隐藏两类尺寸选项并保持原尺寸。旧工作流默认手动。
保持精确宽高比，并取最接近目标倍率的合法偶数网格；特殊尺寸可能偏离标称倍率，
实际结果见阶段报告。质量模式不是倍率：例如 2× 可选择 performance，但最终由
运行库查询裁定，不自动回退。Pipeline Render 的 `scale` 仍保持 100。

尺寸预算统一来自 `sidecar/protocol/sr_limits.json`，同步生成 Python／原生常量。
输出不再按标准 4K 矩形限制：现有 CXR1 RESULT 消息上限为 128 MiB，其中 PTS 占
8 字节，每个 RGBA16F 像素占 8 字节，故 `8 + 宽×高×8 <= 134217728`，最多
16777215 像素。单边 16384 对应 D3D11 纹理边界；仍须满足偶数、精确宽高比及运行库
尺寸查询。736×1280 的 3× 为 2208×3840，消息 67829768 字节，可以通过这层检查。
横竖屏、DCI 类尺寸和相近尺寸使用同一公式，不偷偷缩小。
这是传输硬上限，**不是显存估计或可运行保证**；GPU 中间纹理、SDK 分配、CPU 副本与
编码缓冲另计。阶段报告的 `output_budget` 给出本次实际字节量与限制依据。

本轮只扩大输出，输入仍保留之前的最短边 64、最长边 1920、总像素 2073600 预算；
不因此宣称支持 4K 原始输入、更高分辨率几何或 SR／DLAA 双遍。
旧横屏／竖屏 CXR1 Worker 保持原边界可用，扩大输出需选配新版字节预算 Worker。
旧竖屏 Worker 即使单边合规，也仍有 8294400 像素上限；CSR1 保留独立旧限制。
相机二进制结构和 CXR1 的 128 MiB 消息上限均未改动。

光流的受控比较，以及为何不默认 SR 后再接 DLAA，见[光流与重建对比](flow-comparison.zh-CN.md)。

处理链输出的范围独立于 Preview。`duration=0` 或 `process_to_end=true` 表示剩余全部；
`single_frame=true` 在计算可用前置历史后，仅导出处于／紧随起点的一帧。历史设置提供
前置秒数，最多允许 120 个实际历史帧；不重复执行 NR 新实例预热。历史帧参与计算但不
导出。输入音频从首个输出帧对齐，重编码为 AAC。输出为零起点 CFR H.264 YUV420 SDR，
不提供 HDR 或浮点母版。
两条导出路径都在 AAC 分帧前裁剪音频；短区间尾部时长修复与 Jobs 兼容的诊断传输
见[开发说明](DEVELOPMENT.zh-CN.md#诊断-ui-传输)。

需求报告区分**必需**、**可选且使用**、**不使用**。此路径必须有相机和设备深度；独立
法线／蒙版／置信度没有消费者，不会改善输出。音频只用于主机导出，不是 DLSS 模型
输入。声明齐全不代表 GPU 可用，也不代表估计准确。

## 验证边界

2026-09-14，独立 MapAnything Bridge 的原画幅来源通过 CXR1：736×1280 → 1472×2560，
2×/performance，362 帧、24 fps、15.083 秒，PTS 完整并保留音频；另有 1.5×/quality
及 2×/performance 各 24 帧短段。几何复用已保存的低分辨率 Omega 估计，不是新增
估计器依赖。该实测只证明原网格、时间轴及 GPU 执行，不证明运动画质或估计准确。
当前 SR Worker 已适配非中心投影；本轮不扩展独立 CSR1、RR 或 NR 的 GPU 验收。

有界合成 Linux/Proton 测试已通过 SR（640×360 → 960×540）、DLAA（640×360）、DIS
分支、带三个历史帧的单帧输出，以及带两个历史帧的三帧音视频输出。同样五项已通过
实际 Comfy 执行和 SaveVideo。独立解码确认帧数、零起点 30 fps PTS、SDR 标签及音频
区间对齐。文件核心与 Comfy 的原始输出一致；SR/DLAA 在解码源像素一致时，也与共用
执行器的渲染数据包入口一致。共享执行器后，原有 SR/DLAA/RR/RR+DLAA 四种数据包
模式的回归也均通过。

后续浏览器回归已通过 UI 导入、SR/DLAA 模式切换、单帧／八帧输出、SaveVideo 和
已完成任务列表；四份文件的音视频时长均匹配。独立 CNR1 NR 的单帧／八帧预览及
保存也通过，包括 A/B 加载和逐帧定位。这是有界浏览器验收，不是全面 UI 审核。

下列历史测试使用固定相机的合成场景。估计器准确性、运动
相机 GPU 画质、长视频稳定性、更多浏览器流程、原生 Windows，以及不经过 Proton 的原生
Linux 执行仍未验证。验收不代表已安装估计器或部署到日常 Comfy。

## 光流、深度之外还需哪些数据

| 信息 | 获取或推导方式 |
| --- | --- |
| 相机内参 `K` | 标定或估计实际输入像素网格上的焦距、主点；显式处理去畸变、缩放、补边及裁剪。 |
| 逐帧世界到相机位姿 `[R\|t]` | 从渲染器导出，或重建连续相机轨迹；不能逐帧独立归一化世界坐标和尺度。 |
| 投影、逆投影、FOV、相机位置和轴向 | 从相机模型与位姿数值推导；near/far、reversed-Z 是须与已有深度表示一致的显式约定。 |
| 当前／前一帧裁剪空间变换 | 从相邻帧相机位姿和投影计算，首帧及切镜重置；不需要另一个预测模型。 |
| 帧身份与切镜 | 保留解码源 PTS、文件名到帧的映射、源 SHA-256 和镜头边界；缺失位姿不隐式插值。 |
| Jitter、曝光、色彩 | 成片路径声明零采样 jitter、单位曝光／预曝光缩放，由 SL SR 自动曝光；不恢复 tone mapping 前的辐射值，不伪造渲染 jitter。色彩归一化使用显式输入元数据。 |

Streamline 将无 jitter 的矩阵与采样 jitter 分开，并要求相机和帧间常量。此处使用的
行向量时序组合是 `inverse(P_current) * inverse(V_current) * V_previous * P_previous`。
参见[官方公共常量约定](https://github.com/NVIDIA-RTX/Streamline/blob/main/docs/ProgrammingGuide.md)。

没有相机元数据的素材，可在外部尝试能估计相机内外参的
[VGGT](https://github.com/facebookresearch/vggt)，或进行
[COLMAP SfM 重建](https://colmap.github.io/tutorial.html)。这些节点没有安装、调用或
验收上述估计器。先用短单镜头，保留所有帧与原始 PTS 的映射，转换前通过重投影检查
轨迹。如果估计器处理的是缩放／裁剪网格，要把内参变换回实际输入，或者把该视图正式
保存为新 VIDEO，再重建**全部**绑定该源的数据。

COLMAP 的 `cameras` 保存内参，`images` 保存世界到相机位姿；其 image ID 不是连续
帧号，要按图像文件名恢复帧映射。相机轴为 +X 向右、+Y 向下、+Z 向前，参见
[COLMAP 输出约定](https://colmap.github.io/format.html)。
[VGGT 位姿工具](https://github.com/facebookresearch/vggt/blob/main/vggt/utils/pose_enc.py)
也声明使用 OpenCV 世界到相机外参以及像素内参。

工程建议：独立运动的主体占画面较多时，应围绕静态背景求解相机；切镜分开重建。
真正锁定的机位可以使用固定标定位姿，但不能为了补齐字段而把运动镜头声明为静止。
注册缺失、重投影误差大、未处理的镜头畸变、滚动快门、逐帧尺度漂移，应当触发拒绝或
修正，而不是注入单位矩阵。合成／估计来源必须保留在报告中。

## 版本 1 相机时间线

UTF-8 JSON 顶层必须且只能包含以下字段：

| 字段 | 约定 |
| --- | --- |
| `schema_version`、`kind` | `1`、`"dlss_camera_timeline"` |
| `source` | 仅含 `sha256`、`view_id="source"`、`width`、`height`、`time_origin="video_stream_start"` |
| `fps` | [1,120] 的有理数字符串，如 `"30000/1001"` |
| `depth_inverted` | 与投影及设备深度清单一致的布尔值 |
| `provenance` | `renderer`、`calibrated`、`estimated` 或 `synthetic_test`；只是声明，不是认证 |
| `frames` | 从零开始的每一个 CFR 源帧，1–100000 条，同时受 JSON 32 MiB 上限约束 |

每帧仅含 `pts_ns`、`camera`、`metadata`。`camera` 提供全部
[CameraFrame 字段](reconstruction-nodes.zh-CN.md#版本-1-渲染数据包)。`metadata` 显式填写
`jitter_x=0`、`jitter_y=0`、`motion_scale_x=1`、`motion_scale_y=1`、`pre_exposure=1`、
`exposure_scale=1`、`exposure=1` 和布尔 `reset`。首个源帧必须重置；跳转后首个实际计算
帧也会重置。相机切镜、引导重置或检测到的切镜都会重置历史。

源尺寸受上方横竖屏共用预算限制。矩阵须有限且成对互逆，相机轴须正交归一、宽高比一致，
near/far 投影须符合设备 Z 约定。PTS 最多允许 1 微秒舍入差异，不代表时间重采样。
输入须覆盖所选范围及历史中的每一帧。时间裁切保留源 PTS；物化后的空间裁剪／重编码
有新的源哈希，需要重建输入。执行前重新打开清单；深度平面逐帧读取并验证哈希。

## 纯数值 OpenCV 转换器

`camera_math.camera_from_opencv(K, world_to_camera, ...)` 返回 `CameraFrame`。只在调用时
导入 NumPy，不进行模型推理或文件读写；一致地把 OpenCV 坐标系转换为 Worker 的
+Y 向上坐标系，并建立行向量投影和时序矩阵。提供 `previous_intrinsics` 与
`previous_world_to_camera`，或在首帧／切镜处显式设置 `reset=True`。
使用 `dataclasses.asdict` 序列化，并同时写入 `SLFrameMetadata(reset=...)` 与原始源
`pts_ns`。

转换器现在支持最终 VIDEO 网格上已去畸变、零偏斜的透视内参，包括非中心主点与
`fx != fy`。主点使用从图像边缘起算的像素中心 `i+0.5`；从 OpenCV 整数中心 K
转换时须加半像素。完整投影保留主点偏移，时序变换使用前帧实际 K；不注入采样 jitter。
SR Worker 从投影求视锥宽高比，区别于清单中的像素网格比例。偏斜、非刚性位姿或
缺失前帧位姿会被拒绝，不静默近似。这是导出器
的组成模块，不是自动 COLMAP/VGGT 导入器，更不能保证估计相机运动能改善 DLSS。
转换器已具备 CPU 投影／重投影测试；真实素材重建与主观画质属于另外的验收范围。
