# 视频输入适配

[English](video-input-adapter.en.md) · 简体中文

Audience: public

既有 ID DLSSExperimentalPrepareTemporalSequence 显示为 **DLSS Video Input Adapter**，接受 VIDEO 与 Guides 配置，保持 sequence/report 插槽。它也是输出节点，“检查输入（不运行 NR）”仅排队自身及祖先。Adapter 自身不启动 Worker 或初始化 GPU；但若上游连接了已启用的模型重建节点，检查按钮也会执行该上游，因此不能把整条检查链视为必定不运行模型。

## 信息与状态

卡片显示文件/demuxer、大小、编码/profile、位深、像素格式、编码尺寸、有效 VIDEO 尺寸/裁剪、时间戳、报告帧数、色彩、音频和准备计划。未知字段保持未知；demuxer 名不是唯一容器子类型，名义 fps 不证明 CFR。

状态含 ready、带假设就绪、待确认、不支持、不可读、延后。区分头部可读与完整解码/时序验证。文件 VIDEO 不全解码；tensor/stream 等 VIDEO 在所选区间实体化后验证。卡片标注上次检查，换素材后重查。GPU/Proton 就绪由 Runtime 单独负责。

检查按钮按本次任务 ID 跟踪提交、排队、执行和结束；执行失败、中断或提交失败会解除等待并显示原因。任务成功结束却没有本节点报告时，提示检查上游重建开关、采样兼容性或跳过状态，不再一直显示“检查排队中”。Comfy 队列完成不等于所有下游节点都已执行。

缓存报告只从同一次任务恢复；检查期间修改本节点或上游配置会将返回结果标记为过期。旧报告在等待和无新结果时隐藏，不当成本次成功。活动检查有低频历史／队列核对；连接异常或任务已从队列消失且无历史时显示无法确认，不自动重提任务。节点删除后释放监听和定时器。配置检查卡与输入／处理计划卡复用相同状态处理。

## 缺失色彩的显式策略

| 字段 | 值 | 含义 |
| --- | --- | --- |
| color_policy | strict / fill_missing | 严格标签 / 只补缺失或未知字段 |
| assumed_transfer | bt709 / iec61966-2-1 | 将缺失传递函数解释为 BT.709 / sRGB |
| assumed_range | tv / pc | 缺失范围解释为有限 / 全范围 |
| normalize_to_srgb | false 默认 | 光流/NR 前实际转换到 sRGB 工作 RGB |

fill 模式将缺失矩阵/原色解释为 BT.709。已知标签优先，不支持的已知标签仍拒绝。原信息与有效解释分开，每个假设都有记录。这是**如何解码现有像素的声明**，不是找回真实元数据、改源文件或 HDR 色调映射。

不开归一化时按有效矩阵/范围转换 RGBA8，保留非线性传递约定，输出记录该传递函数。A/B 比较适配原图与 NR 结果；输出标签正确不证明输入假设正确。

## 可选 sRGB 归一化

默认关闭，旧 API 缺省也是关闭。开启时 BT.709 SDR 解码成 full-range RGBA8，再把 RGB 从名义 BT.709 转到 sRGB，然后计算光流/NR。alpha 不变；sRGB 源为精确直通，不重复转换。不改原色（均为 BT.709/D65）、不估白平衡/曝光、不猜缺失标签、不做 HDR 映射。

转换使用 BT.709 逆 OETF 与 IEC sRGB，不是假定 BT.1886 显示变换。这是实验工作空间选项，不证明模型必须 sRGB。8 位 Worker 导致量化，不保证无损往返或高位深/HDR 保留。测试中 BT.709→sRGB→BT.709 的 8 位 LUT 编码前误差最多 1 级；有损编码另有误差。

导出将**原始对照和处理结果**都逆变换回源传递函数，再做 BT.709 YUV/limited 编码与正确标签，不只改标签。报告分开源解释、工作与输出传递，manifest 的 color_pipeline 记录变换。未知/不支持色彩仍阻塞，此开关不覆盖已知 HDR/广色域。

- [BT.709 定义](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.709-6-201506-I%21%21PDF-E.pdf)
- [sRGB 定义](https://registry.color.org/rgb-registry/srgb)

Linux 验证涵盖导出滤镜与独立 Python 逆 LUT 逐字节一致、alpha/直通、带标签视频准备/导出及源 SHA-256 不变。

仍拒绝 HDR 标签/附加元数据、未知原色/矩阵、旋转、非方形像素、非法帧率/尺寸、已知音视频起点不同。上游裁剪的无标签文件 VIDEO 也拒绝，因为 Comfy 实体化可能已按未知解释解码；带标签裁剪使用公开 VIDEO API。通用 VIDEO 在实体化后再次检查，预检不承诺全文件可解码。

## 运动模式

连接 DIS/NVIDIA provider 优先于旧 motion_provider。未连接时：

- dis 默认：CPU DIS 及一致性诊断。
- zero：不创建/执行 DIS，填零向量；仍有切镜和连续 NR 历史，不是 NR bypass 或独立逐帧推理，一致性不可用。

首帧/切镜重置。外部光流张量、逐帧隔离、深度、任意蒙版未实现，见[可连接光流](nvidia-optical-flow.zh-CN.md)。

## 媒体工具

高级 ffmpeg_path/ffprobe_path 接受宿主绝对程序路径或 bin 目录。留空用后端 PATH；指定 FFmpeg 时空 ffprobe 使用同目录配套程序，失败不回退。卡片显示可用性、路径、来源、版本、PyAV 和错误；Preview/Process 沿用配置并记录实际工具，见[媒体工具](media-tools.zh-CN.md)。

## 缓存与执行

准备缓存版本 6 包含源内容、范围/尺寸、Guides、色彩与媒体工具身份。缓存保存**转换后颜色帧和光流**，不只是元数据；Look 强度/风格不在键内。改输入解释、provider 或工具会失效。只准备所选区间，不先转码整视频。

旧 30 秒/2 GiB 限制已移除，当前按磁盘空间准入，见[输出范围与存储](comfy-video-nodes.zh-CN.md#output-range-and-processing-mechanisms)。Preview/Process 在源、范围、缩放、pre-roll、色彩和引导相同时共享缓存。命中显示“颜色/光流缓存已复用”，重启后磁盘缓存仍在，但廉价完整性/头部检查可再次执行，NR 后仍需编码。

不同低清预览不能直接复用全尺寸条目；部分重叠区间不拼接复用。改引导仍重建合并颜色/光流，暂无跨 provider 独立颜色缓存、自动清理或淘汰。开关不删除源/已保存输出。

检查错误以 ready=false 显示，Preview/Process 在 Worker 前拒绝。媒体预检失败会报告失败；不要将旧预览当作新输入结果。

## 验证记录（2026-09-02）

当时 Linux 77 Python 测试通过，纯 macOS 跳过 6 媒体测试；4 JS 检查格式/假设/会话隔离/分支提交。1344×768、24 fps 无标签 H.264 严格模式报告 4 个缺失色彩字段；浏览器仅提交 Load/Guides/Adapter。

显式 BT.709/limited 假设使 DIS/zero 都成功输出 48 帧、672×384 预览，约 6 秒；重复 zero 命中，DIS→zero 不命中。原带标签回归通过。完整输出 124 帧、1344×768，BT.709 标签和音频；视频 5.166667 秒、音频 5.184 秒是包/容器舍入，非样本精确匹配。残留所属进程 0，源哈希不变。

check_input_adapter.py 在独立实例使用节点 1..6 的既有 prompt 复现，仅对测试素材假定 BT.709/limited，不改源或用户工作流。后端更新后重启并打开新页面；旧链接兼容，缺省 strict/dis。刷新前保留未保存工作流。
