# 多层 DLSS NR 轮次

[English](MULTI_PASS_NR.en.md) · 简体中文

Audience: public

`DLSS NR Pass Stack` 用于实验性地把 1–3 个 NR Look 顺序应用到同一段视频。
多层结果可能产生明显的风格重解释，但不是 NVIDIA 官方质量档位，也不是恢复真实细节、
Super Resolution 或 Frame Generation 的保证。

## 接线

```text
NR Look 1 ───────────────┐
NR Look 2（可选）────────┼→ DLSS NR Pass Stack → Preview 的 Look A/B
NR Look 3（可选）────────┘                     → Process 的 profile
```

- `NR 轮次` 为 1–3，默认 2。
- 第 1 层必需。
- 已启用但未连接的第 2/3 层继承上一层 Look。
- 轮次之外仍连接的 Look 被忽略，便于临时降低轮次而不拆线。
- 输出使用与 NR Look 相同的 Comfy 类型，因此现有 Preview/Process 接口和旧单层工作流保持兼容。
- 不允许把 Pass Stack 再接入另一个 Pass Stack；有序层级必须在一个节点中可见。

## 独立效果分支

Comfy 的一个输出本来就可以连接多个下游，因此分支在功能上不需要专用节点。工作流较大时，
可选的 `DLSS 已准备视频分支 Hub` 提供四个内容相同的整理端口：

```text
Video Input Adapter → 已准备视频分支 Hub ─┬→ Look/Stack A → Preview/Process
                                           ├→ Look/Stack B → Preview/Process
                                           └→ Look/Stack C → Process
```

各分支共享输入配置；小型保留条目可复用缓存，大型流式分支会各自解码和计算引导。
Hub 本身不复制像素、不计算光流，也不是并发调度器；当前后端锁仍串行执行渲染任务。
这样，串行多层只由 Pass Stack 表达，互不依赖的效果版本仍用普通工作流分支表达。

## 实际执行语义

```text
准备一次：输入视频 → 颜色 RGBA8 + 原始运动引导 + PTS/切镜 reset

Pass 1：原始颜色 + 原始运动 → NR → 未压缩 RGBA
Pass 2：Pass 1 RGBA + 原始运动 → NR → 未压缩 RGBA
Pass 3：Pass 2 RGBA + 原始运动 → NR → 最终可见 RGBA
                                          ↓
                                   只编码一次 + 原音频
```

当前策略固定为 `reuse_input_guides`：所有层复用 Input Adapter 从原视频估计的同一运动场、
时间戳和切镜标记。不会对生成式中间结果重新计算光流。这与社区常见“上一层颜色 + 原始
MV/Depth”的级联思路更接近，也避免把上一层改变的纹理误当成新的真实运动。

每层拥有独立的时序历史：层开始时 reset，视频内的切镜 reset 继续生效。小型缓存区间按整段
顺序处理每层，可复用兼容常驻进程，但下一层显式重置历史。大型区间逐帧通过各层独立 Worker
并立即编码；这些多层 Worker 在任务后释放。不同 Look 不能在已初始化的旧实例中热切换。

每层 `mix` 都相对于该层输入合成。例如第 2 层 mix=0.5，表示 Pass 1 输出与 Pass 2 NR
输出各占一半，而不是再次与最初原图混合。`nr_enabled=false` 或 mix=0 会旁路该层。

## 存储、缓存与报告

- 小型颜色/光流条目使用内容缓存；大型流式区间修改 Look/轮次后会重新计算引导，见[存储策略](STORAGE.zh-CN.md)。
- 层间使用未压缩 RGBA8：小型缓存模式交换有界文件，大型流式模式交换帧缓冲；最终只编码一次。
- 中间层包含 pre-roll 帧，使下一层获得完整历史；最终输出排除 pre-roll。
- 缓存模式在下一层成功后删除上一层 raw，最终编码后删除最终 raw；流式模式不产生这些文件。
- 当前不缓存 NR 中间层：修改后层 Look 仍会重新执行前层，避免无界保存大型 raw 缓存。
- 报告列出每层 Look、是否继承、设置、原始像素哈希、Worker/常驻信息和逐层性能；顶层明确
  标记 `intermediate_video_encoding=false`。

## 成本与质量风险

两层通常接近两倍 NR Evaluate 与传输成本，三层接近三倍。缓存模式增加 raw 磁盘读写；
流式模式每个启用层保留独立模型实例，增加内存/显存占用。并不
保证线性画质提升。重复生成可能放大：

- 错误光流导致的拖影、切镜后收敛变慢；
- 皮肤、材质、灯光和文字的生成式偏移；
- 颜色/对比漂移、halo、过锐化或结构重写；
- 社区修改模型之间不一致的参数响应。

建议先用单帧比较 1 层与 2 层，再用包含运动和切镜的短片检查稳定性，最后才处理完整视频。
三层默认只作为高级实验；当前不提供 4–30 层“质量滑杆”。未来若自研 Worker 支持多个
Feature 句柄和 GPU 内纹理级联，可以新增能力协商后端，但不能静默改变本页的 host-orchestrated
raw 语义。

## 当前 Linux 验收状态

2026-09-05，把尚未发布的暂存区源码部署到 Linux 的 ComfyUI 0.34.0 隔离测试实例，在
RTX 4070 Ti SUPER、NVIDIA 610.57.04、GE-Proton11-6 及此前验证过的外部 Worker/模型组合上，
完成了一次 50% 尺寸的单帧端到端验收。数据经过 Setup Helper、Sequence Hub 的两个独立
输出及两层 Stack；第二层继承第一层 Look。两层 NR 都成功，最终报告确认没有中间视频编码，
每层残留的所属进程均为 0。第一次生成引导，完全相同的第二次运行命中引导缓存。这只是
单帧集成验收，不代表长片画质/性能、切镜、Windows 或三层已验收。
