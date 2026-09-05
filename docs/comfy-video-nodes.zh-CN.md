# Comfy 视频节点

[English](comfy-video-nodes.en.md) · 简体中文

Audience: public

当前使用 V3 Python 节点与 Comfy DOM widget 扩展，预览在卡片内，不用底部面板；不声称另写了原生 Nodes 2.0 Vue 组件。首次集成验证为 2026-09-02、ComfyUI 0.34.0、前端 1.51.9、Linux RTX 4070 Ti SUPER、GE-Proton 11-6，未增系统依赖。Windows 路径存在但待 GPU 验收。

## 工作流与准备

[示例目录](../example_workflows/README.zh-CN.md)提供 DIS/NVIDIA 预览、双 Look 对比和完整导出。Load Video → Guides/provider → Input Adapter，配 Runtime/Look/History 后进入 Preview 或 Process→原生 Save Video；VIDEO 也可接其他视频节点。

由[模板](../examples/runtime-presets/direct-nr.example.json)创建 direct_nr 预设，组件恰为 relay、worker、nvngx_dlssnr；路径相对 JSON 或绝对。可重复测试显式选 Proton。这条路线不需 ReShade、RenoDX、Streamline、Wine DLL overrides，旧 carrier 诊断独立。

导入只打开文件，需在 Comfy 中保存才进入工作流库；专用测试实例的 user 目录/库独立于主实例。

## Setup Helper 配置预检

在 Runtime Configuration 后添加 **DLSS 配置与环境助手**。建议同时连接 Video Input
Adapter 的 `sequence`，这样会检查其实际自定义 FFmpeg 路径、输入就绪结果及引导/光流选择；
未连接时只检查 PATH 与默认 DIS，并把检查范围不足列为提醒。Helper 的 Runtime 输出可接
Preview/Process，它与输入配置完全相同，没有改写。

节点卡片检查：真实宿主与平台选择、Runtime Configuration 错误、组件存在性/PE 文件头/
当前 SHA-256、已知 Worker/模型组合、Python 媒体包、ffmpeg/ffprobe、NVIDIA 驱动检测，
以及 Linux 下的 Proton/Xwayland/Steam。DIS 检查 OpenCV API；NVIDIA 光流检查 NVOF
helper。高级能力查询只启动本项目宿主原生 NVOF helper 来询问驱动/GPU，始终不会启动
`nvngx.dll` 或加载 `nvngx_dlssnr.dll`。所以全绿预检仍不能替代单帧 GPU 实测。

卡片可复制完整 JSON；其中有本机路径与 GPU 详情，公开粘贴前应删减。节点只诊断，
不会下载、移动、改名、补丁任何文件，也不安装 Python 或系统依赖。

## 节点内预览

设置范围/尺寸，点击“渲染当前帧”或“渲染片段”，只排队该节点及祖先，不触发无关导出。子图内分支提交尚未实现，请用 Comfy 自身执行控制。首次 prefix 可能更慢。

支持滑动分割、并排、交替、差异、播放/暂停和逐帧。差异为浏览器合成，不是定量指标；比较器静音，导出保留音频。播放是漂移校正，不是科学级帧锁播放器；暂停步进把两视频 seek 到相同时间。

A 默认是与 B 同尺寸适配原图，也可接第二套 Look。改 Look 不失效同输入引导缓存。每次预览新 session，node/workflow ID 与递增 revision 拒绝旧/无关事件；后端与临时文件还在时可恢复会话。取消针对本会话，不影响其他任务。

改参数须再次渲染；旧画面不是新参数实时效果。滑动分割线、模式、播放、步进只操作现有结果，不重跑 NR。

## 参数与保存范围

Preview 是实际产出视频的节点。video_b/video_a 保留预览范围、单帧模式和尺寸；Save Video 保存它们不会自动变全视频。卡片渲染按钮不跑下游 Save Video；普通队列按 preview_mode，可执行连接的 Save Video。

| 参数 | 含义 |
| --- | --- |
| start_time | 相对输入 VIDEO（含上游裁剪）起点，秒 |
| process_to_end | 自动用剩余时长并忽略 duration；start=0 为全 VIDEO，单帧仍只一帧 |
| duration | 关闭到末尾时的区间长度；不是结束时间/批大小/预热，遇末尾缩短 |
| preview_scale | 每轴百分比，50% 约四分之一像素；实际改变 NR 输入输出，不是 UI 缩放或 SR |
| preview_mode | 普通队列 range/frame；卡片按钮只覆盖本次，不改保存值 |
| cursor_time | 单帧时刻=start+偏移，偏移小于区间；range 忽略 |
| Look B / A | B 当前效果，A 空为原图，也可另一 Look；不是冻结快照 |
| contract | History Settings，默认真实前置 0.5 秒、冷预热 120 次，不计输出 |

例如 start=10、duration=3、cursor=1.5：片段输出 10–13 秒，单帧选 11.5 秒或之后的解码帧；临近末尾可缩短。单帧 VIDEO 是一帧片段。

正式导出共享 sequence/Runtime/Look 接 Process→Save Video。Process 有独立范围和尺寸，预览不控制它。尺寸 100 为输入尺寸，到末尾输出剩余；兼容旧 duration=0。wipe/side/diff 不改变 A/B 视频或导出合成对比布局。

卡片有保存范围提示和折叠说明；字段标签跟随语言，socket ID/widget 顺序兼容，process_to_end 追加且旧流程默认关闭。该说明更新曾通过 133 Python（4 跳过）、22 JS，283px 内部宽度不横溢，未新跑 GPU 或改用户工作流。

<a id="output-range-and-processing-mechanisms"></a>
## 输出范围与内部处理机制

| 设置 | 所属节点 | 影响保存时长？ |
| --- | --- | --- |
| 起点/到末尾/长度/尺寸 | Preview 或 Process | 范围决定时长，尺寸决定分辨率 |
| 单帧/连续片段 | Preview | 是 |
| 真实前置/初始重复预热 | History | 否，输出排除上下文 |
| 常驻/释放/DLL/Proton | Runtime | 否 |
| 光流/切镜 | Guides/provider | 否 |

连续处理帧，不是独立 2 秒/30 秒批次。小区间使用有界颜色/运动缓存，大区间逐帧解码/光流/NR/编码，不写原始像素或运动文件；全时长不会插入新分段 reset/预热/接缝。payload 逐帧，元数据列表随帧数增长。独立分段并发仍未实现。

没有固定 30 秒视频限制。默认缓存总量 2 GiB、单条原始缓存 128 MiB、临时总量 8 GiB、单任务 2 GiB、磁盘空闲保留 2 GiB，均可配置，见[存储管理](STORAGE.zh-CN.md)。写入期间定期查空间，但不是操作系统预留，其他应用仍可占用。空间不足、非法时间或超过 Worker **1,000,000 帧**预算明确失败，不暗中截短。请求范围最多 **86,400 秒**；一小时墙钟 watchdog 是执行时间，慢任务仍可超时。不活动准备缓存按最近使用淘汰；临时预览须用户明确清理。报告有解析范围和存储计划。

常驻 65,536 帧是复用容量，不是输出长度。更长任务用单独隔离 Worker，并记 resident_fallback，不拆成重置分段。

全时长回归曾有 138 Linux Python（4 opt-in 跳过）、23 JS。check_full_duration.py 验证 36 秒/864 帧预览（duration 仍填 2）、手动 4 秒、35 秒单帧及独立 Process→SaveVideo 全 36 秒含音频；所属进程均清理。只是超过 30 秒样例，不代表任意电影都适合磁盘/watchdog。大于 2 GiB 准入由单测覆盖，GPU 小样例不额外写数 GiB。

两张自绘卡片可缩窄，根无固定最小宽度，控件换行、长诊断断行、垂直滚动。不要恢复旧 520/380px 最小宽或内容撑宽 grid。实测输入内部 269px、预览 266px 无横溢。

## 控制职责

- Guides/provider：DIS/NVIDIA/zero、分析尺寸、切镜、一致性，不管 NR 外观。
- NR Look：开关、强度、可读风格/预设、局部色调/结构/皮肤、自动 mask、mix、高级 UI/Worker 配置，见[参数响应](nr-look.zh-CN.md)。艺术预设不是 SR Quality/Balanced。
- NR Pass Stack：把 1–3 个 Look 连接成有序级联，输出直接接 Preview/Process 原 Look 插口；
  缺少的后层继承上一层。层间用未压缩 RGBA、复用原始引导、最终只编码一次，见
  [多层 NR](MULTI_PASS_NR.zh-CN.md)。
- 已准备视频分支 Hub：Input Adapter 后的可选四路接线整理节点。各输出引用同一份准备缓存，
  不复制帧、不重算光流、不执行 NR，也不启用 GPU 并发。直接把一个 Comfy 输出连接到多个
  下游同样有效。
- Runtime：后端、组件、平台/Proton、进程策略，不含艺术参数。
- Setup Helper：只读校验该 Runtime 及已连接输入/光流依赖，不拥有渲染参数。
- Preview/Process：范围/尺寸，不复制 Look。自动预览需防抖/有界范围/旧结果处理，当前未实现。
- Input Adapter：兼容性、色彩、[宿主媒体工具](media-tools.zh-CN.md)；History：预热/前置。

D5V2 一帧颜色+运动进、一帧同尺寸颜色出；无独立输出尺寸或帧倍率，SR/FG 未实现。mix 是 8 位非线性后混合，0 或 NR 关闭不启动 Worker。pre-roll 默认 0.5 秒且不越上游裁剪，warmup 默认 120，两者不同。

## 存储与生命周期

COMFY_DLSS_HOME 可覆盖默认 ComfyUI/user/default/comfy-dlss-experimental：

- prepared-clips/：颜色/运动 spool、manifest。Preview Session 与 Process Video
  均有“保留输入准备缓存”开关，默认开启；关闭后仅在任务成功时删除该任务的精确条目。
  失败时保留便于重试，并发使用者尚未结束时不会误删。
- runtime-snapshots/：三组件哈希校验副本，非硬链接。
- prefixes/：按运行时/平台/Proton/显示分开的 prefix。
- cache/、tmp/：Worker 环境缓存；executions/：任务记录。

Comfy temp 中 dlss-experimental/<session>/a、b 和报告。Process 输出直到 Save Video 才正式保存。完成任务 raw 输出删除；小型准备数据默认保留，也可由上述单任务开关在成功后删除。大型区间流式处理，不写准备 raw。新增 **DLSS Storage Manager** 可查看、复制、移除准确识别的不活动生成条目及修改上限；不删除正式输出、源视频、运行时快照或 prefix。

换 DLL 改快照/prefix key。默认每 variant 隔离；可选常驻仅复用已验证哈希对和兼容 NR/尺寸/运行时，按需启动、任务 reset、默认空闲 300 秒（30–900）释放。关闭后空闲立即释放或等任务结束，也可手动释放。不同 A/B Look 需替换，不热改，见[常驻](preview-performance.zh-CN.md#opt-in-resident-worker)。Linux 按唯一 marker/pidfd 清理，禁止泛化名称 kill。

## 测试结果

544×960、24 fps、8 秒 SDR 首次集成：2 秒/272×480/48 帧预览约 5–6 秒；重复新会话命中缓存；mix=0 无 Worker。帧处理中取消，残留 0，Comfy history 可能记错误但卡片显示取消。全输出 192 帧、8 秒音视频约 19 秒，保留 BT.709/sRGB；浏览器 A/B 加载、暂停步进对齐。

自动测试涵盖分支隔离、媒体报告、旧选项迁移；GPU/媒体需依赖及 opt-in。24 Look 测试、双 Look、全导出通过，默认/重复 raw 哈希相同，见[实测矩阵](nr-look.zh-CN.md#measured-response)。

check_comfy_video_nodes.py 会排队 GPU、测试取消并保存视频，只用于预配空闲独立实例；传 --prompt、--report、可选 --url，不在繁忙生产队列擅跑。

## 限制与迁移

输入只支持 CFR、方形像素、SDR BT.709 原色/矩阵与 BT.709/sRGB；缺失标签须显式解释，HDR/旋转/VFR/超限明确拒绝。全时长支持不等于长视频重叠流水线。无合成深度、SR、FG；部分 Look 未见响应，异常音频起点仍待改进。

旧 schema-1 Look/Guides/render 拒绝，需用当前模板重建。后端更新重启 Comfy，前端更新刷新；组件指纹过期/字节变化启动前拒绝。单独测试实例须明确 --database-url、--base-directory、--user-directory，避免 Comfy 迁移主实例旧数据库。
