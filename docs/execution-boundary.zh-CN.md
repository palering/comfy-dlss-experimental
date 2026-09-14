# 宿主与执行层边界

[English](execution-boundary.en.md) · 简体中文

Audience: public

完整视频的 NR 与 SR 调度已有不依赖宿主的 Python 入口。ComfyUI 仍是面向用户的集成；
这里是内部 API，不是已安装的 CLI、稳定的外部 RPC 协议或新的运行后端。

```text
Comfy VIDEO → ComfyVideoSource ─┐
                              ├→ 共用 Python 执行器 → 原生 Worker
文件路径    → FileVideoSource ──┘       → 输出 Path + report
                                               → Comfy 包装 VIDEO
```

## 职责

| 层 | 负责内容 |
| --- | --- |
| `comfy_adapter.py` 与节点端点 | 公共 VIDEO 的裁剪／截取物化、Comfy 路径、取消／进度适配、把输出包装成 VIDEO。 |
| `video_source.py` | 源时长及所请求时间区间的绑定。文件入口不会静默忽略空间裁剪。 |
| `execution_context.py` | 显式绝对数据／临时目录，以及调用方提供的同步进度／取消回调。 |
| `nr_execution.py` / `sr_execution.py` | 输入检查、准备、调度、报告及输出文件结果。 |
| 媒体、引导、进程模块 | 编解码、源哈希／PTS、数值引导、缓存上限、已认证二进制传输与所属进程清理。 |
| C++ 功能引擎 | 设备／资源／fence 及厂商功能生命周期；不解码媒体，不依赖 ComfyUI。 |

`render_nr_video(source=..., context=..., sequence=..., runtime=..., contract=...,
profile=...)` 接受 `VideoSource` 和已分离的第 2 版 sequence 元数据。
sequence 不能含 `video`；引导设置仍使用节点 payload 结构（包括 `schema_version: 2`
以及百分数 `analysis_scale`），不是内部 `GuideSettings` 数据类的表示。
runtime 必须已通过检查并绑定精确文件。原有范围与缓存策略继续生效。

SR 的 `render_sr_video` 使用 `SRVideoInput`：已分离的 sequence 元数据及数值深度
provider。`load_guide_manifest` 可在没有 VIDEO 对象时打开该 provider。Comfy 适配层
在分离前检查图中的源对象身份；执行器仍检查实际源哈希、视图、网格与 PTS。
解除对象绑定不会把相对深度估计变成已校准设备深度。见 [SR 要求](super-resolution.zh-CN.md)。

`ExecutionContext` 将目录作用域限定在任务内；使用同一临时根的任务共享配额预留，
常驻兼容键也包含数据根。这不提供跨进程调度，也不允许并发调用同一原生会话的 GPU。
调用方仍须保证每个常驻会话只有一个所有者。

## 保持不变的边界

- Python 不加载厂商 DLL。旧 D5V2/CLR1 与自有 CNR1/CSR1 仍是不同协议；stdout 不是帧通道。
- Comfy 端点保留节点 ID 与 VIDEO 输出；A/B 预览、服务器／UI 监控仍是 Comfy 集成，不是独立应用。
- 取消停止宿主继续提交并清理所属进程，不中断已经执行中的 GPU kernel。
- 脱离 Comfy 仍需要媒体依赖、数值校验、磁盘限制及后端验收。能够独立导入不等于 GPU 验收。
- 独立 [Streamline CXR1 开发后端](streamline-reconstruction.zh-CN.md)接受显式相机／RR 平面，
  不是普通 VIDEO 输入，不改变 `owned_sr` 或其能力声明。宿主无关的 `render_reconstruction`
  文件执行器与薄 [Comfy 重建节点](reconstruction-nodes.zh-CN.md)共享输入、运行库快照、
  编码和取消边界。
