# 可连接光流与 NVIDIA NVOFA

[English](nvidia-optical-flow.en.md) · 简体中文

Audience: public

## 接线

DLSS Optical Flow · NVIDIA 或 DIS 接到 Video Guides.flow_provider；Guides 配置与源 VIDEO 一起进入 Input Adapter，sequence 再进入 Preview/Process。provider 输出可序列化配置，不立刻计算全视频张量，只在消费时处理所选区间及 pre-roll。

Guides 仍控制分析缩放、切镜与前后向一致性；Look 控制外观。连接 provider 优先于旧 motion_provider；不接则兼容旧 DIS/zero。选 NVIDIA 不可用时不会暗中回退。

NVIDIA 提供速度/均衡/质量、4×4/2×2/1×1 网格、时序 hints、CUDA 设备编号。网格支持由驱动验证，更密不保证 NR 更好。Linux GPU 已验证，Windows helper 可交叉编译但待实机验证，见[分发](distribution.zh-CN.md)。

## 构建与依赖

Linux 上从节点根目录运行：

```sh
bash sidecar/build_nvof.sh
sidecar/build/dlss-nvof-helper --probe 0
```

使用现有 Zig、C++20、CUDA Driver API 头文件。默认头文件目录 /opt/cuda/targets/x86_64-linux/include，可用 NVOF_CUDA_INCLUDE 覆盖。脚本不安装软件；无 CUDA kernel、nvcc、TensorRT、权重、FreeImage、OpenCV CUDA 或新增 Python 包。运行时加载驱动提供的 libcuda.so.1 / libnvidia-opticalflow.so.1，不打包它们。

锁定的两个官方头文件是 **NVOF API 2.0**，不是当前文档 API 5.0 结构；支持所需 CUDA 调用及 1/2/4 网格。前后向用两个独立会话防止 hints 串方向，不是 API 5.0 的合并调用。

- [锁定 SDK 源码](https://github.com/NVIDIA/NVIDIAOpticalFlowSDK/tree/edb50da3cf849840d680249aa6dbef248ebce2ca)
- [NVIDIA 编程指南](https://docs.nvidia.com/video-technologies/optical-flow-sdk/nvofa-programming-guide/index.html)

## 数据与生命周期

Python 解码 RGBA8，生成分析分辨率的 uint8 灰度，每个片段请求使用一个原生 helper。二进制有界管道传前帧/当前帧、索引、reset；helper 校验尺寸、协议及设备限制，尊重 GPU row pitch，读取前显式同步。

输出每个方向分量是有符号 S10.5，除以 32 得输入像素位移。网格插值**不能再乘网格大小**。Python 稠密化、恢复处理尺寸像素单位并打包小端 float16 XY。方向始终当前→前帧、左上原点、右/下为正；D5V2 不变。

首帧/切镜为零向量并重置 NR 历史；下次估计前使两个 NVOF 历史失效。helper 在区间内复用，NR 开始前回收。光流没有 daemon/listener，不依赖 Proton 或显示服务器。不声称独占 GPU；现有缓存锁串行准备片段。

管道等待可取消、每次交换 30 秒期限，stderr 有界，异常/正常完成均关闭准确子进程。C++ 使用不可复制 RAII；无法确认 GPU 完成时直接终止，不析构可能仍使用中的资源。

缓存包含 provider 参数、helper SHA-256、驱动信息、设备选择、来源/范围/色彩。能力探测不等于计算成功；缓存命中仍验证可用性。manifest 记录后端与逐帧指标。一致性只是诊断，不传 confidence/cost/depth/mask 到 NR。

## Linux 验证（2026-09-03）

RTX 4070 Ti SUPER 报告网格 1/2/4、单轴 32..8192；适配器仍采用现有流水线更严格限制。已有 CUDA 头文件和 Zig 足够，没有改系统依赖或主 Comfy 环境。

- 合成 (+6,+4) 平移在全部网格、50%/100% 分析尺寸得到中位 (-6,-4)；另测静止与 reset。
- 三个 preset 均执行；取消、非法输入、日志上限、子进程回收有自动测试。
- 独立 Comfy 实测旧 DIS、连接 DIS、NVIDIA 4×4/1×1 均输出 12 帧；重复请求命中，改网格生成不同缓存。

```sh
DLSS_TEST_NVOF=1 python -m unittest discover -s tests -p 'test_flow_provider.py' -v
```

使用已有媒体 Python；check_nvidia_flow.py 是显式测试服务器脚本。make_nvidia_flow_workflow.py SOURCE DESTINATION 保留运行时配置创建**新** UI 工作流，拒绝覆盖。

上述测试不证明优于 DIS。没有实现长视频流水线、零拷贝、external hints、cost 平面、学习式模型或新的 NR G-buffer 输入。
