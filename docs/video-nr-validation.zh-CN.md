# 真实视频 NR 验证

[English](video-nr-validation.en.md) · 简体中文

Audience: public

诊断分离三层：media_clip.py 区间/时间戳解码导出；temporal_guides.py CPU 光流与历史重置；direct_nr.py / native_relay.py 协议监管。run_video_nr_test.py 串联显式测试；Comfy 经 video_pipeline.py 共用，见[节点](comfy-video-nodes.zh-CN.md)，不是另一个预览应用或底部面板。

## 依赖与范围

Linux 使用已有 NumPy、DIS OpenCV、PyAV、Pillow，以及独立宿主 FFmpeg/ffprobe 命令，后者不因 PyAV 就自动存在，见[媒体工具](media-tools.zh-CN.md)。当时版本 NumPy 2.4.6、OpenCV 5.0.0、PyAV 18.0.0、FFmpeg 9.0.1，未装额外包/工具链。纯测试无需媒体依赖，缺失时显式跳过集成测试。

支持 BT.709 原色/矩阵、BT.709 或 sRGB 传递、方形像素、偶数输出尺寸、CFR。拒绝 HDR/未知色彩、旋转、变尺寸、VFR/不连续时间戳。最初诊断限制 30 秒、2 GiB；当前节点改为可用磁盘准入并支持全时长，见[当前限制](comfy-video-nodes.zh-CN.md#output-range-and-processing-mechanisms)。pre-roll 最多 5 秒，准备仍写磁盘、未做阶段重叠。

只保存选区和前置上下文，不放整视频 RAM；seek 可能从更早关键帧解码，源校验仍读整个文件。准备数据保留便于复现，不自动删；源及标签不变。报告不包含任意容器标签（含嵌入工作流 JSON）。

## 时间、运动与色彩

起点/时长相对视频流起点；保留纳秒 PTS，对照有理帧率验证。pre-roll 进 NR 但不输出，重复首帧 warmup 与真实上下文不同。A/B 从可见帧零开始，帧集相同；可见区间音频重编码 AAC，不逐字节复制。异常音视频起点需单独验证。

DIS 估计当前→前帧、左上原点像素位移；缩小分析的流插值并恢复每轴像素尺度，再打包小端 FP16。已知 (+6,+4) 平移应得到约 (-6,-4)，全/半尺寸都如此。[OpenCV 约定](https://docs.opencv.org/4.x/d4/dee/tutorial_optical_flow.html)中将当前帧作为第一输入，匹配已检查的 converter 接口；这是社区版本兼容证据，不是官方 Feature 18 规范。

首帧、显式 reset、检测切镜使运动为零并重置。切镜用可调灰度差阈值，快速运动/照明可能误判。双向一致性/重投影误差只是诊断，不是 reactive mask，也不证明质量。

默认按明确 YUV 矩阵/范围转换 RGBA，保留非线性 SDR 值；可选 [sRGB 归一化](video-input-adapter.zh-CN.md)增加工作传递变换及导出逆变换。导出 limited BT.709 YUV 并标源 transfer。测试 FFmpeg/libx264 只设 codec 标志时丢原色/transfer，加 setparams 帧元数据后修复。最终文件发布前检查色彩、实际帧数、fps、音频；无损 PNG 用于区分模型与编码变化。

## 使用

按[中继说明](direct-nr-relay.zh-CN.md)准备 relay/外部运行时/Proton/隔离目录与 Xwayland DISPLAY：

```bash
python scripts/run_video_nr_test.py \
  --source /path/to/source.mp4 --root /path/to/dlss/direct-nr-experimental \
  --relay /path/to/dlss-native-relay.exe --worker /path/to/runtime/nvngx.dll \
  --proton '/path/to/installed/Proton/proton' \
  --start 1 --duration 2 --pre-roll 0.5 --modes zero dis

python scripts/run_video_nr_test.py \
  --source /path/to/source.mp4 --root /path/to/dlss/direct-nr-experimental \
  --relay /path/to/dlss-native-relay.exe --worker /path/to/runtime/nvngx.dll \
  --proton '/path/to/installed/Proton/proton' \
  --start 0 --duration 8 --pre-roll 0 --modes dis --intensities 0.25 1.0
```

video/<run-id>/ 含 prepared/manifest.json、颜色/运动 spool、original.mp4、各 variant 的 processed.mp4、PNG、日志、JSON。passed 仅指协议/导出校验，不代表更好看；visual_quality_approved 需评审才可改为 true。

## 初始证据（2026-09-02）

544×960、24 fps 动画人像不缩放，最初 2 秒比较用 12 上下文+48 可见帧+120 预热，各 180 次评估。zero/DIS 完整返回并清理，显存回 3 MiB。

59 次转换的灰度匹配平均误差：不扭曲 12.75，反向流 4.48，简单负流 17.91。只检查对应，不是输出质量。4c62f7f7f676 的初始 MP4 丢 transfer/原色，已废弃替代，PNG/raw 证据仍有效；外观比较用之后已校验导出。

0b2287ec16bd 以 0.25/1.0 共用准备，处理完整 192 帧/8 秒；每种 312 评估、192 不同帧、无所属残留。音视频各 8 秒，sRGB transfer、BT.709 原色/矩阵、limited、24 fps 均验证。准备约 4.8 秒，每 Worker 22 秒，含导出双版本总 55 秒，非通用性能。高强度改变明暗/材质外观，偏好与时序伪影仍需主观检查。

56 Linux 测试覆盖平移符号/尺度、切镜、reset、非法平面、CFR 舍入、色彩/旋转拒绝、范围/pre-roll、独立解码对比、音频/标签、覆盖拒绝、取消与源完整性；纯 macOS 跳过 4 媒体项。

后续媒体工作包括主观检查、长视频流水线、精确音频偏移/VFR。预设、卡片 A/B、取消与旧预览抑制已在独立 Comfy 集成中接通测试。
