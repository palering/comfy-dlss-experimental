# Windows sidecar 与原生辅助程序

[English](README.en.md) · 简体中文

Audience: public

当前正式视频路径使用 **dlss-native-relay.exe + 用户外部 Worker/模型**，不是下文保留的 NGX bootstrap。relay 用已有 Zig 编译，不需要 Windows/MSVC 构建机、NGX SDK 或 ReShade。见[直接 NR](../docs/direct-nr-relay.zh-CN.md)与[分发](../docs/distribution.zh-CN.md)。NVIDIA 光流 helper 另按宿主平台编译。

图形 DLL 必须留在独立进程，不进 Comfy Python。Windows 原生执行 PE，Linux 经用户选择 Proton。以下是保留的加载、GPU 拷贝与 ABI 诊断，不是安装必做步骤，也不是当前 NR 输入契约。

## 诊断协议与解析器

控制/帧格式见 protocol/sidecar-v1.schema.json 与[frame-stream-v1](protocol/frame-stream-v1.zh-CN.md)。Python codec 在 comfy_dlss_experimental/sidecar_protocol.py，独立于 Comfy；对应 C++20 有界解析器 src/frame_protocol.cpp 可在无 Wine/GPU/专有文件的宿主测试：

```bash
bash sidecar/build_protocol_test.sh
```

## Loader probe

src/probe.cpp 创建设备，加载用户 nvngx_dlssnr.dll，检查 NGX D3D12 导出，向 stdout 或可选第二路径写 JSON，**不评估帧**。

```bash
bash sidecar/build_probe.sh
```

生成 sidecar/build/dlss-sidecar-probe.exe。已有 Zig 提供 Windows 头文件/库，无需 llvm-mingw 或系统 MinGW。用户 DLL 留在仓库跟踪外，不随项目分发。

## D3D12 carrier

src/carrier.cpp 加载应用目录 dxgi.dll（ReShade），创建 D3D12 12_0 与隐藏窗口 swapchain，以有界 Present 心跳让 ReShade 加载 renodx-dlss5.addon64。JSON 明确尚未实现 DLSS 帧评估。

```bash
bash sidecar/build_carrier.sh
```

仅在隔离任务目录运行，放 dlss-carrier.exe、重命名为 dxgi.dll 的 ReShade、renodx-dlss5.addon64、nvngx_dlss.dll、nvngx_dlssnr.dll。ReShade.ini 只补缺省设置。隐藏窗口仍需 Wine/Proton 图形会话；程序不安装/选择 Proton。

run_carrier_smoke.sh 将副本放新、不复用的 job 目录，使用指定 Proton/prefix，子进程覆盖为：

```text
d3dcompiler_47=n;dxgi=n,b
```

七个路径均显式传入，不搜索/下载专有组件。

## GPU 帧流诊断

--frame-worker 保留设备/队列进入二进制协议。Windows 可标准流；Proton launcher 会重定向，Linux 用 --tcp 127.0.0.1 PORT TOKEN_HEX 回连临时本地端口，先验证随机 256-bit token。

必需颜色为输入尺寸 RGBA8_UNORM / RGBA16_FLOAT，可选 RG16_FLOAT motion、R32_FLOAT depth、R8_UNORM reactive mask、1×1 R32_FLOAT exposure。上传 default heap，转换 copy dest→source，限时 fence 后回读；输出紧密行 OUTPUT_COLOR 与所有引导进行字节验证。**不做转换、估计或 NGX 评估**。

HELLO 为 ngx:false/configuration_supported:false，CONFIGURE accepted:false，不能暗示 Look 生效。无配置仍可拷贝，但输入输出尺寸必须相同。

Worker 在创建 swapchain 后不做 flip-model Present 心跳，因为离屏拷贝不需要；普通 bootstrap 保留心跳。旧停顿确认来自 Proton 标准流，不是已证明 Present 背压。仍检查 RenoDX 已从 D3D12 hook 同步加载，否则拒绝。

这是实际 GPU 传输测试，用于隔离协议、Proton 管道、row pitch、资源屏障、同步/回读，不是最终视觉效果：

```bash
python scripts/run_frame_worker_smoke.py \
  --preset /path/to/runtime-preset.json \
  --proton /path/to/proton \
  --data-root /path/to/test-data \
  --display-backend xwayland
```

一 Worker 测 15 帧/39 平面、交替尺寸/格式、3 reset、index/PTS/有效行字节，含奇数尺寸 padding、FP16 HDR 值、有符号运动、深度/曝光/蒙版。成功还需 ReShade/RenoDX/NVIDIA adapter/swapchain、干净退出和 0 返回码；报告为 frame-stream-report.json / frame-worker-result.json。

同步客户端先完成本帧写再读响应，不先发送全视频。stderr 写 job log，不放无人读取管道。Linux 失败监管按启动组/精确 job 标记及 pidfd，不用宽泛命令正则；此诊断不是生产池。

```bash
python -m unittest discover -s tests -v
SANITIZE=1 bash sidecar/build_protocol_test.sh
bash sidecar/build_carrier.sh
```

## 历史合成 NGX 研究

src/ngx_smoke.cpp 是有界合成 DLSS/DLAA Create/Evaluate 探测，动态解析驱动 bootstrap nvngx.dll，不链接/打包 nvsdk_ngx_d.lib。仅构建时需仓库外 SDK 头文件：

```bash
NGX_SDK_INCLUDE=/path/to/NVIDIA-DLSS/include bash sidecar/build_renderer.sh
```

它是负对照，不是当前渲染器。目标机动态重建导出 ABI 在选定 NVIDIA adapter 后仍在 NGX 内崩溃，而 SDK 链接对照能初始化，保留用于复现；崩溃由 job 隔离，不是 Comfy/Python 崩溃。

该历史 SDK 静态 archive 使用 MSVC /MD、msvcprt、MSVCRT、异常展开、stack cookie、线程安全静态初始化。Zig MinGW 不能安全直接链接它，禁止伪造 CRT/SEH 符号。**这不是当前 direct relay 的构建要求**；未来官方 SDK 后端需按其工具链契约重新设计。

Linux 诊断明确选择 Xwayland（WINE_GRAPHICS_DRIVER=x11 + DISPLAY）或实验原生 Wayland（wayland + WAYLAND_DISPLAY），使用不同 prefix。当前 direct NR 只验证/支持 Xwayland。
