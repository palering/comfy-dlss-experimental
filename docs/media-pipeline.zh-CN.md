# 输入组装与功能处理链

[English](media-pipeline.en.md) · 简体中文

Audience: public

实验性处理链节点将素材检查、引导选择、效果阶段分开。原工作流、Look/Stack 参数、
Runtime 预设和外部 D5V2 Worker 继续兼容。可选[自有 NR 运行时](owned-runtime.zh-CN.md)
也使用该处理链。独立的[实验性 SR/DLAA 路径](super-resolution.zh-CN.md)已接入
SR Stage → Pipeline Render，需要启用 SDK 的 Worker 与数值深度，尚未通过 GPU
验收。这些节点不会生成深度或 FG 插帧。

[Streamline VIDEO 路径](streamline-video-input.zh-CN.md)则把同一个组装器连接到
**Streamline SR / DLAA 阶段**和处理链输出，需要显式相机／设备深度及 `owned_sl` / CXR1。
该独立路径已通过有界合成 Linux GPU 和实际 Comfy 验收，不代表 CSR1 已验收。

## 连接方式

```text
加载视频 → Video Input Adapter ─────────────────┐
DIS Flow ──── a ┐                              │
NVIDIA Flow ─ b ├─ Flow Selector → Input Assembler → NR Stage → Pipeline Preview
               ┘                  ↑                ↑   └──→ Pipeline Render → 保存视频
                             Video Guides        NR Look
```

Runtime 分别接到预览／输出。可选 History Settings 控制前置历史与预热，不决定输出
范围。同一输出插槽可以连接多个消费者，不需要专门增加 hub 节点。

导入[处理链示例](../example_workflows/dlss_pipeline_preview.json)，选择自己的视频，
按[现有安装步骤](distribution.zh-CN.md)配置 `runtime-presets/default.json`。
示例默认选择 DIS（`a`），第一层 NR 开启、第二层关闭，预览为 50% 尺寸单帧。
安装原生 NVIDIA 光流 helper 后再切换到 `b`。节点架构变化不需要新增运行 DLL。

## 各节点职责

### 模式联动与保留值

Streamline Stage 切到 DLAA 时隐藏手动输出宽高，后端保持输入尺寸；切回 SR 恢复原值。
SR 新增按 1.5×／2×／3× 从当前输入自动计算尺寸；倍率模式也隐藏手动宽高。
旧工作流默认为手动；切换不覆盖原值，运行后报告实际尺寸，详见 [SR 倍率与预算](streamline-video-input.zh-CN.md)。
Pipeline Render、Process Video、Pipeline Preview、Preview Session 勾选“处理到视频末尾”时
隐藏手动时长，但保留起点；只有起点 0 才覆盖全视频。单帧仍只输出一帧，Render/Process
的时长 0 兼容语义不变。预览和正式输出的范围互相独立。

底部提示解释当前设置，不自动运行或刷新旧报告。连线控制模式时不依据本地值隐藏参数。
隐藏不删除值、不调整控件顺序、不改变连线；保存并重载旧工作流继续兼容。更新后先保存
工作流再刷新页面。界面使用已有依赖，不更改 Worker、模型、采样或时间契约。

| 节点 | 负责什么 | 是否渲染 |
| --- | --- | --- |
| Video Input Adapter | 检查素材头信息、色彩策略和媒体工具；原 Guides 输入改为可选。 | 不运行 NR，实际准备按需执行。 |
| Flow Selector | 选择 `a`、`b` 或 `c`，只向所选惰性输入请求数据。 | 否。 |
| 外部数值引导／选择器 | 读取／选择绑定同一 VIDEO 的数值引导声明，实际逐帧读取延后。 | 否。 |
| Input Assembler | 保留 VIDEO 引用，切换已配置／外部运动，附加深度／相机／法线／蒙版／置信度并明确使用状态。 | 否。 |
| 相机时间线输入 | 读取绑定来源的相机／帧元数据，不运行相机估计器。 | 否。 |
| Streamline SR / DLAA 阶段 | 在纯输入链上声明单个 CXR1 阶段及输出尺寸。 | 否，处理链输出使用 owned_sl。 |
| NR Stage | 添加一个 Look 或 Pass Stack，可开关。 | 否。 |
| SR / DLAA Stage | 在没有效果阶段的输入链声明单个 SR/DLAA 阶段及明确输出尺寸。 | 否，延后交给 SDK Worker 执行。 |
| Pipeline Preview | 处理指定 NR 单帧／片段，节点内 A/B、滑轴与诊断。 | 是，仅 NR；拒绝 SR。 |
| Pipeline Render | 按独立输出范围分发 NR 或单个实验 SR/DLAA 阶段。SR 尺寸由 SR Stage 控制，Render 保持 100%。 | 接到保存视频等输出消费者，并且运行时／输入齐全时执行。 |

选择器不会在所选输入断开或错误时自动换算法。若未选方案同时连接到另一个输出节点，
它仍可能为那个消费者执行；惰性选择不取消图中其他独立通路。

## 引导优先级与检查

Input Assembler 的 `motion_source` 默认 `configured`：直接连接的光流提供者优先，
其次为连接的 Video Guides，再其次为 Input Adapter 旧设置。分析尺寸、运动缩放和
切镜阈值放在 Guides；DIS/NVIDIA 算法选项放在对应提供者。组装图时配置提供者不会计算帧。

切换 `external` 后改为请求 `external_motion`，准备时按素材／哈希／PTS 校验读取
当前到前帧像素运动，不运行 DIS/NVIDIA，也不静默回退。未选中的外部运动分支是惰性输入。
可选 `depth`、`normals`、`mask`、`confidence` 保留有类型数值引导附件，但**不影响 NR
输出**。文件、单位、时间契约见[外部数值引导](external-guides.zh-CN.md)。

Assembler、NR Stage、SR Stage 与 Streamline Stage 卡片提供“检查计划（不渲染）”和“复制详情”。检查只提交本节点
及祖先，不提交下游渲染／保存。它不会创建光流估计器、初始化 NR，也不能证明 GPU
支持。素材头信息错误会保留显示；时间戳与内容校验在执行请求区间时完成。
卡片显示上一次收到的结果，上游配置变更后请重新检查；诊断报告不写进保存的工作流。

可将最后一个功能阶段的 pipeline 输出连接到 Setup Helper 新增的可选 `pipeline`
输入，检查组装后实际选择的媒体工具和光流依赖。它优先于 Helper 单独的 `sequence`
和 `flow_provider` 输入。Helper 保守地检查所选配置，即使当前 NR 阶段关闭也会检查
该光流依赖；不会启动模型。原来需要主动勾选的 NVOF 能力查询仍独立保留。

## 串行阶段与分支

串联 NR Stage 可依次使用不同 Look；重复设置可使用 Look/Stack 输入。
当前最多 **三轮**，包括接入 Stack 内的轮次和关闭的阶段。关闭是直通，不移除插槽。
后层接收前层未压缩结果，不在各层之间编码 MP4。运动、时间戳与切镜标记仍复用原素材，
不会在每次风格化后重新估计，详见[多层 NR 契约](MULTI_PASS_NR.zh-CN.md)。

从组装／阶段输出引出分支，可试验独立效果或预览较早的阶段。各分支配置彼此隔离，
不共享可变 NR 历史。多分支**不代表保证 GPU 同时执行**。小型保留缓存可按原缓存键复用，
大区间流式任务会重新计算引导。如果 B 和可选对照 A 都直通，新处理链会省去光流计算。

预览的可选 Look/Stack A 仍应用于同一份准备后的素材；不接 A 就以输入作为对照。
预览 VIDEO 输出保留预览尺寸和时长。输出完整视频时，将最后一个 NR Stage 分支接到
Pipeline Render，选择 100% 和“处理到视频末尾”，然后把 VIDEO 连接保存视频。
卡片内预览按钮不会触发这条完整输出通路。

## 独立 SR/DLAA 分支

把没有效果阶段的 Input Assembler 输出接到 **SR / DLAA Stage**，同时连接
SR Settings，之后连接 **Pipeline Render → Save Video**。提供匹配的 `device_z`
深度和独立 `owned_sr` 运行时；输入归一化到 sRGB，Render 保持 100%，关闭 Worker
常驻。当前 SR 媒体执行器逐帧流式处理，不保留准备后的原始帧缓存，也不重复 NR 预热。

首版只允许单个 SR/DLAA 阶段，混合 NR/SR 链、多次 SR 和 SR A/B Preview 均明确拒绝。
可以从同一组装素材引出独立 NR、SR 分支，但这不是效果叠加，也不保证 GPU 并发。
导入 [SR 连接模板](../example_workflows/dlss_sr_experimental.json)前，请先阅读
[SR 输入要求与构建边界](super-resolution.zh-CN.md)。

## 数据与兼容边界

处理链传递的是不透明 VIDEO 引用和独立配置，不是整段视频解码后的张量。
NR 的色彩归一化、引导计算、取消、Worker 常驻和[存储配额](STORAGE.zh-CN.md)沿用原执行器。
修改 Look 不会把效果参数加入引导缓存键。

媒体适配器提供 RGBA8 颜色，以及按 RG16F 打包、以像素为单位的当前帧到前帧运动。
自有 NR 将颜色转换为 CNR1 的 FP16 传输；旧 D5V2 保持原传输。
外部深度／法线／蒙版／置信度端口是数值元数据附件，不是估计器实现或实际 NR 输入；
报告明确标为两种 NR 后端均不消费。不会因附件存在就假定已有几何、光照或光追信息。

SR 执行器会消费附加的设备深度数值平面，而不是深度可视化图，同时使用准备后的颜色
和运动。它使用 CSR1 与官方 SDK，不经过 NR caller；运行时绑定、帧尺寸及功能支持
独立校验。其他附件仍明确标为 SR 不消费。

新链路有便携契约、节点接口和前端逻辑测试，但不能替代实际 ComfyUI 导入、浏览器
布局与 Windows/Linux GPU 验收。不要覆盖正在使用的预设，也不要拿 caller shim
替换旧外部 Worker；自有 Worker 与[薄 caller](caller-shim.zh-CN.md)是独立组件，
通过显式自有运行时预设选择。
