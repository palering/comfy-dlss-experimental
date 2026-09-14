# 外部数值引导

[English](external-guides.en.md) · 简体中文

Audience: public

外部引导节点将独立光流或深度估计器的**数值输出**接入输入处理链。它们不会下载模型、
运行估计器、猜测缺失的相机参数，也不会把彩色光流图／深度预览图自动变成数值缓冲。

## 当前可以做什么

- **外部数值引导 / External Numerical Guide** 读取 JSON 清单，并绑定到 Video Input
  Adapter 的同一份 `sequence`；`guide` 类型为 `DLSS_GUIDE_PROVIDER`，`report` 输出可复制元数据。
- **外部引导选择器 / External Guide Selector** 选择 `a`、`b` 或 `c`，只请求所选惰性
  分支；所选输入缺失或无效时直接报错，不静默回退。
- **Input Assembler** 提供 `external_motion`、`depth`、`normals`、`mask`、`confidence`
  接口。使用外部运动场时将“运动来源”切为“外部数值引导”；默认“已配置光流估计器”
  保持原有 DIS／NVIDIA 行为。
- NR 会按请求帧读取所选外部运动场。深度、法线、蒙版、置信度目前只是有类型附件：
  **NR 不消费它们**，连接上不代表新增光照、几何或光追输入。

```text
Video Input Adapter ────────────────────────────→ Input Assembler → NR Stage → 预览／输出
        ├→ 外部数值引导（运动）────────────────→ external_motion
        └→ 外部数值引导（深度）────────────────→ depth（NR 仅声明元数据）
DIS/NVIDIA Flow ───────────────────────────────→ flow_provider
```

引导加载器和组装节点连接同一份已检查的序列。比较内置与外部光流时显式切换
`motion_source`；比较多种外部算法时使用外部引导选择器。若某个未选分支还被其他输出
节点独立请求，这些开关不会取消那个消费者的执行。

## 清单与文件

估计器输出放在自己管理的目录中，条件允许时放在 Git 仓库之外。`manifest_path`
是 **ComfyUI 宿主机**上的路径，不是浏览器所在电脑的路径。逐帧文件使用相对清单目录
的路径，不能越出该目录。节点按需读取单帧，不会一次把整个视频的引导读入内存。

以下是两帧运动清单模板。源文件和逐帧文件的哈希都要替换为实际的小写 SHA-256，
全零占位符并不是你的文件的有效内容标识。预览、前置历史和正式输出所需的每一帧都要列出。

```json
{
  "schema_version": 1,
  "source": {
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "view_id": "source",
    "time_origin": "video_stream_start",
    "width": 640,
    "height": 360
  },
  "role": "motion",
  "semantics": "current_to_previous_pixels_top_left_xy",
  "units": "input_pixels",
  "grid": {"width": 640, "height": 360, "sampling": "pixel_centers"},
  "dtype": "float32_le",
  "storage": "raw",
  "frames": [
    {"path": "motion/000000.f32", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "pts_ns": 0, "reset": true},
    {"path": "motion/000001.f32", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "pts_ns": 33333333}
  ],
  "metadata": {"includes_camera_motion": true, "includes_jitter": false}
}
```

`raw` 文件无头部，使用紧密排列的行优先、通道交错数据：运动形状为 `[高度, 宽度, 2]`，
先 X 后 Y，小端 float16 或 float32。也支持数值型、C 顺序、形状和类型匹配的 `.npy`
1.0／2.0；不接受对象数组、pickle、`.npz`、PNG 或 RGB 光流可视化图。单通道 `.npy`
可以是 `[高度, 宽度]` 或 `[高度, 宽度, 1]`。逐帧哈希覆盖完整存储文件，包括 `.npy` 文件头。

## 运动契约与对齐

- NR 要求**当前帧 → 前一帧**，左上角为原点，X/Y 位移单位是输入像素。
  前向光流不能只取负就当作后向光流；加载器能声明前向或归一化 UV 光流，但 NR 会拒绝它们。
- 包含相机运动，不包含投影抖动。使用原始 `source` 视图，网格必须等于源视频完整尺寸。
  预览缩放会重采样运动场，并分别缩放水平／垂直位移数值。
- `source.sha256` 必须匹配执行器实际消费的源文件／物化文件。最简单的起点是未修改、
  直接加载文件的 Load Video；同一场景的另一个编码文件，其哈希不能互换。直接引用源文件
  的裁剪快速路径保留原文件哈希和时间轴，不要把引导时间戳重置为裁剪片段的零点。
  其他变换或内存 VIDEO 物化后需要使用匹配／重建的引导。
- `source.time_origin` 必须为 `video_stream_start`；`pts_ns` 为呈现时间减去视频流起始时间，
  单位是整数纳秒，严格递增，既不是容器原始 PTS，也不是任意截取片段的相对零点。请求帧要在
  1,000 ns 舍入容差内唯一匹配，不会取最近帧、插值或静默重采样时间轴。
- 在切镜／不连续处设置 `reset: true`，第一张引导自动重置历史。除可见预览帧之外，
  还应提供前置历史帧的引导。
- 数值必须有限。单独的有效性／置信度图目前不会替 NR 屏蔽无效向量，需在导入前修复运动场。

清单哈希参与引导缓存标识。实际读取所选帧时检查内容哈希、精确形状／长度及有限值。
组装后修改清单需要重跑引导节点；修改帧文件却不更新清单，会在读取该帧时失败。
已准备输入缓存遵循原[存储策略](STORAGE.zh-CN.md)，外部输入文件仍由用户自行管理。

## 其他数值角色

| 角色 | 语义／单位 | 额外声明 |
| --- | --- | --- |
| 深度 | `linear_view_z`：`meters` 或 `relative`；`inverse_view_z`：`inverse_meters` 或 `relative` | 元数据为空，数值严格为正。 |
| 设备深度 | `device_z`、`zero_to_one` | `reversed_z`、有限 `near`／`far`、`projection` 为 `perspective` 或 `orthographic`；数值 [0, 1]。 |
| 法线 | `view_space_xyz` 或 `world_space_xyz`、`unit_vector` | 三个有限分量，范围 [-1, 1]；元数据为空。 |
| 置信度 | `motion_confidence` 或 `depth_confidence`、`zero_to_one` | 单通道 [0, 1]；元数据为空。 |
| 蒙版 | `validity`、`reactive` 或 `transparency_composition`、`zero_to_one` | 单通道 [0, 1]；元数据为空。 |

单目相对深度不是设备深度，不应只改标签来通过校验；相机／投影假设和转换步骤必须明确。
另见不执行渲染的 [SR 输入计划](super-resolution.zh-CN.md)，以及现有 NR 的
[处理链连接说明](media-pipeline.zh-CN.md)。

限制：清单 ≤8 MiB、1–100,000 个帧条目、单帧数值数据 ≤256 MiB。逐帧文件不会放进
节点 helper 包，本项目不分发第三方模型权重。便携契约测试不代表对 WAFT、U2Flow、
MegaFlow 等估计器做过质量评测，也不等于外部引导已完成 GPU 实机验收。
