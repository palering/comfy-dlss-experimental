# Windows sidecar 与原生辅助程序

[English](README.en.md) · 简体中文

Audience: public

## Streamline CXR1 重建 Worker 与诊断

独立 [CXR1 开发后端](../docs/streamline-reconstruction.zh-CN.md)已将显式 Python
相机／SR／RR 输入接到该功能引擎。用 `build_sl_worker.py` 构建，输出
`comfy-dlss-sl-worker.exe --serve-sl`，不是默认 NR／CSR1 Worker。
其受控 GPU／传输验证不同于下述合成探测；显式
[owned_sl 重建节点](../docs/reconstruction-nodes.zh-CN.md)也已通过有界 Comfy SaveVideo 集成测试。

`sl_sr_engine.*` 是独立 C++ 功能适配器，要求显式数值相机常量，不含 Comfy、媒体或
传输代码。`sl_sr_probe.cpp` 仅提供合成静态棋盘场景：RGBA16F 颜色、零 RG16F 运动与
平面 R32F 设备深度。它不是 `owned_sr` 实现，也不是视频节点。

使用已有 Zig 和用户提供的官方 Streamline 头文件构建：

```bash
python3 sidecar/build_sl_sr_probe.py --streamline-include /path/to/streamline/include --native-tests
```

此交叉构建动态查找 SL 导出，不链接 NGX／SL 导入库，不安装 MSVC sysroot。
输出 `sidecar/build/sl-sr-probe/comfy-dlss-sl-sr-probe.exe` 及构建／哈希报告。
需另行提供匹配的 `sl.interposer.dll`、`sl.common.dll`、`sl.dlss.dll`、`nvngx_dlss.dll`
及它们的运行时依赖；构建脚本不下载或复制厂商文件。

诊断参数依次为绝对运行库目录、新建任务目录、`device|frame|sequence` 和非零项目 UUID，
最后可加 `sr|dlaa|rr|rr-dlaa`（默认 `sr`）。RR 另需 `sl.dlss_d.dll`、`nvngx_dlssd.dll`，
测试素材显式提供合成材质缓冲。
按该顺序分阶段运行，外围须有超时和隔离的进程所有权。它写入 JSONL 事件和 FP16 输出，
通过隐藏 swapchain 每帧执行一次真实挂钩 Present，满足 SL 生命周期记账；处理结果从
独立离屏纹理回读。未启用 OTA 标志。GPU 等待与完整诊断均有时限，失败不表示可以安全自动重试。

使用所提供 SL 2.14.1 二进制，在 Linux/Proton 上已通过有界 640×360 → 960×540
合成场景的设备／Present、单帧和八帧推理；所有通道有限，第五帧重置后精确重现前四帧。
这不是自然视频画质、长时间、原生 Windows、完整 ABI、CSR1 传输或 Comfy SR 验收。
CSR1 当前没有该适配器要求的相机元数据，适配器不会替视频静默编造它；SL SR 公共接口
也未暴露 NGX 的帧时间参数。原有后端选择和默认 Worker 能力声明不变。

当前正式视频路径使用 **dlss-native-relay.exe + 用户外部 Worker/模型**，不是下文保留的 NGX bootstrap。relay 用已有 Zig 编译，不需要 Windows/MSVC 构建机、NGX SDK 或 ReShade。见[直接 NR](../docs/direct-nr-relay.zh-CN.md)与[分发](../docs/distribution.zh-CN.md)。NVIDIA 光流 helper 另按宿主平台编译。

可选重构路径：[自有 NR 运行时](../docs/owned-runtime.zh-CN.md)使用本项目 Worker 与薄 caller；
[SR/DLAA](../docs/super-resolution.zh-CN.md)使用同一 Worker 的官方 SDK 构建，不用 NR caller。
SR 源码/CPU 检查不代表已链接或已通过 GPU 验收。旧 relay 预设不会自动迁移，以下保留旧路径与诊断背景。

图形 DLL 必须留在独立进程，不进 Comfy Python。Windows 原生执行 PE，Linux 经用户选择 Proton。以下是保留的加载、GPU 拷贝与 ABI 诊断，不是安装必做步骤，也不是当前 NR 输入契约。

独立的[自有 caller shim](../docs/caller-shim.zh-CN.md) 是带类型转发测试的开发组件，
当前视频路径不使用它，不可替换外部 Worker。可选构建需要官方 SDK 头文件，
普通 relay 安装仍不需要这些头文件。

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
