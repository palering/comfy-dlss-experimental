# 预览、执行记录与性能

[English](preview-performance.en.md) · 简体中文

Audience: public

## 已实现行为

- **渲染当前帧**输出游标处或之后的一帧。游标相对 start_time 且在 duration 内；真实前置帧仍评估。冷实例执行预热，兼容常驻实例保留 feature 并重置历史。单帧是调 Look 工具，不证明时序质量，也不保证与长历史连续推理中的同帧逐像素一致。
- **渲染片段**用于运动、闪烁、切镜检查。普通 Comfy 排队使用 preview_mode；卡片按钮只覆盖本次预览分支，不改保存值、不执行 Save Video。
- 未有结果前游标只是时间选择器（UI 步进回退 24 fps）；渲染后用源帧率。拖动不逐次解码未渲染源图；单帧结果停在其记录时间直到下次完成。
- 控件分为渲染操作、四等宽对比模式、分割线、播放/时间轴；按钮有 pressed/focus 状态，窄卡片显式换行。
- 颜色/光流独立于 Look 缓存；适配原始 A 的编码也缓存在 prepared-clips/<key>/original-preview-v1/，校验长度及 SHA256后复制到 Comfy temp 供 /view 使用。连接 Look 的 A 是额外 NR 渲染，不用原图缓存。
- Preview Session 与 Process Video 默认保留该准备条目。关闭“保留输入准备缓存”后，只在任务成功时删除这一条精确缓存；失败时保留便于重试。并发分支使用租约计数，任一分支要求保留或执行失败都会阻止删除。该选项不会删除源/输出视频、DLL、运行时快照或 Proton prefix。
- 性能改进没有省略必要重建、历史、色彩转换或质量设置。

## 诊断位置

Preview 显示节点执行总墙钟时间和阶段明细；展开后随卡片高度分配空间，不再限制 680px 卡片/180px 详情。Runtime 的“Worker 实时状态”与“执行记录 · 历史快照”分开。可见展开卡片在执行或有匹配常驻时每秒更新，无 Worker 时每 5 秒，并响应 Comfy 执行变化；折叠/隐藏停止轮询。整卡 GPU 单独折叠，仅作参考。

所属进程 RSS 包含 Proton 并列出进程，Worker 显存按 Linux PID 对应。旧采样明确历史，不当作当前空闲占用。空闲常驻也有当前资源采样。

“终止并释放当前 Worker”需确认，会取消准确 execution ID 的任务；不能任意指定 PID。空闲时显示“释放空闲 Worker”。释放针对精确 worker ID，忙时还核对 execution ID，避免旧按钮取消后续任务。接受请求不等于释放成功；状态有 starting/running/idle/releasing/released/cleanup_failed。无 Worker 按钮禁用，缓存保留，取消不自动重启。

“本次任务详情 → 复制任务详情”复制**已执行** JSON：参数、尺寸、缓存、阶段时间、后端/Proton、组件哈希、错误与可用资源采样，不拿未提交参数替代。普通 HTTP Clipboard API 不可用时尝试选中文本复制，仍失败则保留只读报告供 Cmd/Ctrl+C。公开前检查路径/环境隐私。

每次实际 Preview/Process 写入 COMFY_DLSS_HOME 下 executions/<execution-id>.json，记录实际参数、组件/源哈希、色彩/引导、后端证据、缓存、耗时、结果及资源。完整数据留磁盘；服务返回最近 15 条，卡片最多显示匹配 preset 的 10 条已完成记录；活动任务只在实时区。重启保留记录。

### 统计限制

- 时间只覆盖 DLSS 节点执行，不含排队与全部上游。上游先失败由 Comfy history 负责；整节点缓存命中不是新执行。
- first_frame_and_warmup / nr_frames_and_transport 是含传输/同步的宿主墙钟时间，不是纯 GPU kernel 时间。
- color_conversion、optical_flow、cache_write 包含在 input_preparation 内，不能父子相加。
- 资源每秒采样（旧记录 2 秒）；短任务可能无采样，也可能漏峰值。仅归属带标记的 Linux relay/Proton/Worker；RSS 相加可能重复计算共享内存。CPU 是累计 CPU 秒，不是瞬时百分比。
- GPU 利用率/总显存是整卡。Worker 显存匹配 NVIDIA graphics/compute PID，缺失/不支持保持不可用而非假 0。Windows 暂无等价所属进程 RSS 收集。
- SHA256 是精确组件身份，尚不解析产品/FileVersion，不是人类版本号。
- 磁盘执行记录保留最近 1,000 条；准备条目受容量 LRU 和单任务保留选项管理。临时任务受配额保护，可在[存储管理节点](STORAGE.zh-CN.md)中明确删除。
- 默认隔离，常驻需开启；打开监控或开关不会启动模型或渲染。
- worker_startup 截止到 relay 子进程已创建握手，不代表 NR-ready。首帧包含初始化、传输、预热与结果，不是单独 120 次评估时间。协议没有更细时间戳。复用使用 history_reset_first_frame；冷 startup 包含在 worker_acquire 中，不能重复相加。

<a id="opt-in-resident-worker"></a>
## 可选常驻 Worker

Runtime“按需启动并常驻”默认关闭：按需启动、NR 完成释放。开启后首次真实渲染才启动，默认空闲 **300 秒**释放，高级 idle_timeout_seconds 可选 30–900。关闭会释放匹配空闲实例，或等当前任务结束。加载工作流不自动释放另一工作流实例；切到显存密集流程可手动释放。

D5V2 只读取一次控制头。常驻保留有界流：65,536 帧容量，最长 1,800 秒年龄。任务首帧显式 reset，保留真实 pre-roll，复用时不重复初次 120 次预热。仅 resident_worker.py 中已验证 Worker/模型 SHA256 对允许此模式，其他组合须隔离并独立验证。不需要新 DLL/原生构建。

改 NR 参数、尺寸、组件哈希、Proton/显示环境会重建；只改 Python mix 不重建。因此同 Look 拖游标/重复预览受益，改 Look 或不同 A/B 仍有冷启动。没有实时参数重配置或多个并行 Worker。租用/清理串行，任务之间不声称 EOF，逐帧校验且 exit:null，另报常驻生命周期。

失败、部分、取消或不健康流丢弃；超时、手动释放、容量/年龄、配置不兼容及服务退出会清理。清理失败保留 prefix 锁并阻止替换。历史记录可能显示当时回到 idle，只有实时区说明现在状态、ID、哈希、租用次数、空闲倒计时、RSS/显存。

Linux/Proton、NVIDIA flow、24 fps、0.5 秒 pre-roll、冷预热 120、准备缓存命中：

| 单帧预览 | 隔离/冷 | 常驻复用 |
| --- | ---: | ---: |
| 280×376，相同位置 | 2.84 秒 | 0.51 秒 |
| 560×752 | 尺寸改变后 3.43 秒 | 0.63 秒 |

测试冷/复用、seek复用/隔离的 raw 哈希相同；独立 12 帧 reset 探测也逐字节相同，非全素材时序等价承诺。Look/尺寸触发重建；手动、真实 30 秒空闲、复用中取消均清理。用 check_resident_worker.py 和 check_worker_controls.py --resident 在独立空闲实例复现。

## 已确认热点与修复（2026-09-03）

同步回环 TCP 分开写小头部/数据触发 Nagle 与延迟 ACK 每帧等待。现 Python 与 C++ 两端均设 TCP_NODELAY；只改 Python 不足。不改系统 TCP 设置，见 [Winsock 说明](https://learn.microsoft.com/en-us/windows/win32/winsock/tcp-ip-characteristics-2)。

RTX 4070 Ti SUPER、GE-Proton 11-6、Feature 18、NVIDIA flow、intensity 0.4、warmup 120、同准备缓存/Look：

| 工作量 | 修复前 | TCP 修复 | 再加 A 编码缓存 |
| --- | ---: | ---: | ---: |
| 2 秒、336×504 | 5.31 秒 | 3.37 秒 | 未单独测试 |
| 10 秒、672×1008、220 帧 | 26.77 秒 | 12.89 秒 | 10.18 秒 |
| 普通帧中位、336×504 | 47.7 ms | 7.0 ms | — |
| 普通帧中位、672×1008 | 91.4 ms | 19.5 ms | 18.1 ms |

所有 raw 输出哈希相同，包括 220 帧原尺寸。不是跨素材/硬件速度保证。原尺寸首次准备约 5.85 秒（光流 3.77、像素转换 0.17），命中约 0.24；结果编码约 2.35；A 编码从 2.49 降到命中 0.005 秒。

336×504、游标 0.5 秒单帧约 2.8 秒，主要 startup 0.74 与 first/warmup 1.18 秒。一个输出帧不是总共一次评估。采样所属 RSS 约 1.35 GiB，Worker 显存 749 MiB，不是并发准入的安全峰值。

check_preview_performance.py 在独立空闲实例复现；--runtime-preset 比较旧/新 relay 而不换 DLL；--with-frame 检查单帧与 Look 更改。不要同时竞争跑基准。

## 后续优化与并发边界

1. 测量完整性读、管道/socket 拷贝、上传/回读和同步。cache_read_verify 单列缓存检查；GPU 时间戳需 Worker 支持。减少拷贝须有字节等价/取消回归，不能直接取消完整性检查。
2. 小型缓存区间复用 A 编码；大型区间已使用有界 decode/flow→NR→管道编码，不写原始帧文件，见[存储策略](STORAGE.zh-CN.md)。编解码器/模型工作区仍占额外内存。后续编码改动须另验质量/色彩/音频；本次不更换 NVENC 或 CRF。
3. 常驻已避免兼容任务的初始化；Look 变化仍需新实例。进一步优化需验证重配置协议或其他 Worker，不是仅保留 Python。
4. 分段并发**未开启**。GPU/prefix 锁仍单任务；多实例需要独立 prefix/会话、准入和各尺寸峰值 RAM/VRAM 与安全余量，不能仅据瞬时空闲显存。
5. 镜头内分段丢历史，需真实前置帧、独立预热、重叠丢弃、顺序 PTS/音频拼接及连续结果对照；固定重叠不保证等价。优先已确认切镜，只在速度收益且无内存压力/接缝时试双 Worker，否则退回 1。本版不声称动态分段加速。

[NVIDIA SMI 文档](https://docs.nvidia.com/deploy/nvidia-smi/index.html)区分整卡利用率、显存与进程报告；实现调用宿主工具，无新增 NVML/psutil。

## 验证记录

TCP 改动时 Linux 118 Python、9 JS 通过，9 relay mock（echo、日志洪泛、退出、截断、拒绝、取消、断开、超时、阻塞写取消）全部通过。实际测试单帧/区间、Look 缓存、归一化、Process→Save Video，不改源和厂商 DLL。UI 检查修复四模式按钮换行问题，但非完整无障碍/拖缩矩阵。

常驻后续 131 Python（4 opt-in 跳过）、21 JS 通过，覆盖懒加载、不可变兼容键、复用、超时/容量、过期释放、取消、部分流、清理失败。浏览器确认开关默认 off、打开不启动、渲染后 idle 显示资源/倒计时、关闭后准确实例清理且残留 0；未重复所有历史 GPU 测试。
