# Streamline 重建开发后端

[English](streamline-reconstruction.en.md) · 简体中文

Audience: public

## 当前状态与边界

独立 CXR1 Worker 通过公开 Streamline 接口执行 SR、原分辨率 DLAA、光线重建 RR，
以及使用 DLAA 分辨率模式的 RR。Python 发送显式相机与数值平面；Worker 持有 D3D12、
Streamline、fence 与输出回读。不使用 NR caller shim，也不链接 NGX 静态导入库。
现有预设、CNR1／CSR1 二进制与 Comfy 节点不会自动切换。

使用所提供 SL 2.14.1 二进制，在 Linux/Proton 的受控短序列中，四种模式均通过
Python→Worker→GPU 的实际执行；输出与对应独立重建探测程序逐字节一致。
这不代表自然视频画质、任意相机／运动正确性、长时间稳定性、原生 Windows 或
长篇实际素材已验收。显式[重建节点](reconstruction-nodes.zh-CN.md)已另行通过四种模式的
有界 Comfy 渲染 → SaveVideo 测试，以及单帧预览输出。

这是开发 API，不是普通 VIDEO→RR 节点。RR 要求真实渲染器／材质数据；单目相对深度、
RGB 法线可视化与光流本身不能补齐该契约。本实现不估计相机矩阵，也不伪造缺失的 G-buffer。

## 构建与运行库

```bash
python3 sidecar/build_sl_worker.py --streamline-include /path/to/streamline/include --native-tests
```

使用现有 Zig Windows-GNU 交叉构建，输出
`sidecar/build/sl-worker/comfy-dlss-sl-worker.exe`。相机、RR 和协议的原生测试启用
地址／未定义行为 sanitizer。脚本不下载或打包专有文件，不安装编译器／sysroot。

显式运行库目录须包含用户提供的兼容文件：`sl.interposer.dll`、`sl.common.dll`、
`NvLowLatencyVk.dll`、`sl.dlss.dll`、`nvngx_dlss.dll`、`sl.dlss_d.dll`、`nvngx_dlssd.dll`。
当前启动器即使只跑 SR 也要求完整 SR/RR 文件集。文件存在不代表设备支持或版本兼容。
`owned_sl` 预设及文件／节点执行器会核验精确组件哈希并使用隔离快照；直接调用底层 API
的开发者仍须自行固定、隔离运行库。
未启用 OTA 标志。

使用 `OwnedWorkerProcess(..., feature="sl", caller=None, project_id=...)`，项目 UUID
必须为规范格式、非零。Linux 要求显式已有 Proton 可执行文件、专用测试前缀与可用图形会话。
macOS 可构建及测试契约，但不能执行该 GPU 后端。隐藏记账窗口不等于无需显示服务器的无头运行。

## Python 输入

宿主无关类型位于 `comfy_dlss_experimental.sl_contract`；客户端为
`sl_worker.ReconstructionClient`，通过进程所有者的 `client` 取得。

| 输入 | 要求的表示 |
| --- | --- |
| 设置 | 显式输入／输出尺寸，`feature="sr"` 或 `"rr"`，质量模式或 `"dlaa"`；严格保持宽高比、不缩小 |
| 颜色 | 紧密小端 RGBA16F；声明 `srgb` 或 `linear_sdr`，RR 必须为 `linear_sdr` |
| 运动 | RG16F，从当前指向前帧的输入像素 XY 位移、左上角约定，包含相机运动，不含 jitter |
| 深度 | R32F，有限 [0,1] 设备深度，与倒置深度设置相符 |
| 相机 | 四个行主序、无投影抖动 4×4 矩阵：view→clip 及逆、当前 clip→前帧 clip 及逆；位置、正交单位轴、投影参数 |
| 帧元数据 | 真实输入像素 jitter、运动缩放、预曝光、曝光比例、RR 曝光及 reset；`unjittered_video` 不接受伪造 jitter |
| RR 附加数据 | 输入分辨率的线性漫反射／镜面反射 RGBA16F 反照率、RGBA16F 单位 XYZ 法线＋线性粗糙度、RG16F 反射运动、互逆 world↔view 矩阵 |

RR 使用默认 RR preset 和显式正数 1×1 曝光。SR 使用自动曝光，未使用的手动曝光字段
须保持 1。第一版契约不支持 HDR、含 jitter 的运动、镜面命中距离替代路线或其他可选 RR 缓冲。

调用顺序：

```python
client.create(settings, session_id=1)
output_rgba16f = client.process(
    color_rgba16f, motion_rg16f, device_depth_r32f, pts_ns,
    camera=camera_frame, metadata=frame_metadata, rr=rr_guides_or_none,
)
client.end()       # 结束本任务；下个任务第一帧重置历史。
client.release()   # 更换设置前释放功能。
owner.shutdown()  # 关闭传输，仅回收属于自己的子进程。
```

无论输入错误、传输失败还是取消，都应在 `finally` 中关闭进程所有者。`process()` 返回
原始输出像素，不是 VIDEO 对象或编码文件。调用者须提供与素材匹配的元数据，逐帧消费
输出，并另行实现编码／存储。Python 检查平面长度和元数据；原生引擎在上传前独立检查
尺寸、有限数值及引导语义。这些检查都不能证明声明确实对应原始渲染器。

## CXR1 协议与生命周期

全部小端。32 字节 `<4I2Q>` 头依次为 magic `0x31525843`、版本 1、消息类型、
负载长度、有序请求 ID、会话 ID。认证、单会话／单在途控制消息和 CER1 类型化错误
复用自有传输的结构，但 CXR1 不接受 CNR1／CSR1 头。当前 CER1 将 Streamline 错误
标为 Worker 域，不误标为 NGX 返回码。

CREATE 为 40 字节：已校验的 32 字节 SR 设置、功能 ID（1=SR，2=RR）、jitter 策略
（0=无抖动，1=外部真实元数据）。拒绝 HDR 与含 jitter 的运动标志。能力响应声明编译
支持的 SR|DLAA|RR 位，不代表当前显卡支持；初始化时另查设备。

FRAME 依次为 `<QII7fI>` 元数据（48 字节）、`<80fI>` 相机（324 字节）、颜色、运动、
深度。七个浮点为 jitter XY、运动缩放 XY、预曝光、曝光比例和曝光；两个保留整数须为零。
RR 再追加 32 个矩阵浮点以及漫反射、镜面反射、法线／粗糙度、反射运动平面。
SR 帧负载精确为 `372 + 16*input_pixels`，RR 为 `500 + 44*input_pixels`。
RESULT 为原 uint64 PTS 后接输出 RGBA16F。携带 PTS 不代表 SL 消费了 NGX 的帧时间参数；
该 API 没有暴露它。

尺寸使用[横竖屏共用传输预算](streamline-video-input.zh-CN.md)：输入最短边 64、最长边
1920、总像素 2073600；输出最长边 3840、总像素 8294400。单消息 128 MiB，每会话一百万次
评估；任务内 PTS 非负、严格递增且不超过有符号 64 位范围。DLAA 输入／输出同尺寸，
SR 输出必须更大。所选质量模式的实际允许尺寸仍由运行库最优尺寸查询决定。

第一帧及显式切镜重置历史。RR 在切镜时明确释放／重建功能历史：测试中单独 reset 位
不能完全隔离旧 RR 历史。END 保留引擎但重置任务 PTS，RELEASE 释放引擎。
有界测试也覆盖 SR 模式 DLAA 与 RR 的 END 复用、释放再创建。GPU 完成 fence 后才回读／
复用；若 fence 或清理未确认，终止该 Worker，不展开可能仍在 GPU 使用中的资源所有者。

## FG 与 MFG 是独立功能

`fg_contract.py` 定义有界时间戳／切镜时间线与显式硬件能力门槛，不是帧生成渲染器或
Comfy 节点。受控 Present 捕获实验已取得真实中间帧，但测试配置要求窗口保持前台焦点。
通用帧／PTS 关联、丢帧处理与后台执行尚未验收，未接入 CXR1。不支持的 MFG 会被拒绝，
不会用反复普通 FG 或其他
插帧器冒充。

### FG 窗口焦点与暂停选项

**当前已测试的 FG Present 捕获路径要求 GPU 主机上的 Worker 窗口持续处于前台并持有焦点。**
这不是要求 ComfyUI 浏览器标签页一直选中。测试运行库在失焦时停用插帧，日志为
`DLSS-G disabled: window not focused`。这是当前运行库／路径的实测限制，不代表已证明
硬件或算法必须如此。NR 及已测试的 SR／DLAA／RR 重建路径不要求前台焦点；后者仍使用
隐藏窗口与图形会话。

有界 FG 实验在启动时申请焦点，每个输入帧前检查焦点；获取失败或失焦就终止。
清理时仅在 FG 窗口仍持有焦点、原窗口仍存在的情况下尝试恢复原焦点，不反复抢占前台。
申请焦点不保证成功；窗口可见或置顶也不等于持有前台焦点。Windows 限制
[SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow)
的使用，Linux/Proton 也需在实际图形会话中验收。

[官方 DLSS-G 指南第 6.4 节](https://github.com/NVIDIA-RTX/Streamline/blob/main/docs/ProgrammingGuideDLSS_G.md#64-disabling-frame-generation)
规定：`DLSSGOptions::mode = DLSSGMode::eOff` 关闭帧生成，默认释放所分配的资源。
在 `flags` 中加入 `DLSSGFlags::eRetainResourcesWhenOff` 后，关闭生成时保留资源，
减少重新启用时的分配开销。它是资源保留标志，不是另一种 mode，也不会绕过焦点检查或
让后台继续插帧。带此标志长期停用时，需要先关闭功能再显式释放资源。

将来可以用这两个选项实现暂停／恢复，但还必须暂停读取输入及推进输出时间线，恢复时
重新准备时序历史并验证生成帧对应关系。此功能与独立图形会话方案均尚未实现或验收。
不能把缺少生成帧的输出静默当作 FG 成功结果编码。

当前诊断使用 `FG_FOREGROUND_REQUIRED`：FG 能力查询输出作用域为 `tested_present_capture`
的 `execution_notice`，并标明 `focus_checked=false`；该查询不创建窗口，也不检测实际焦点。
Python 契约中“离线输出未验证”的错误也包含同一要求。这些诊断不代表已注册 Comfy FG
节点，也不代表后台执行已通过。

其他路径见[现有 SR 节点](super-resolution.zh-CN.md)、[执行边界](execution-boundary.zh-CN.md)
与 [Sidecar 诊断](../sidecar/README.zh-CN.md)。
