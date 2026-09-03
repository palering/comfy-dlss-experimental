# 帧流协议 v1

[English](frame-stream-v1.en.md) · 简体中文

Audience: public

这是保留的 GPU 拷贝诊断协议，**不是当前 NR 的 D5V2**。在 Comfy Python 与隔离 Windows D3D12/NGX Worker 间传规范帧，不包含 NVIDIA 二进制，不要求 Python 加载 ReShade/RenoDX/NGX。

可用二进制标准流或可靠有序流（如回环 TCP）。Linux/Proton 因启动器重定向标准流而使用已认证回环 TCP；认证前导独立于本文包格式。

整数均小端；发送完整包，接收拒绝未知版本/消息/flag/格式/语义、非零 reserved、不一致大小及超限包。Python 参考 codec 上限 512 MiB、单轴 16384。

## 包头（32 字节）

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| magic | char[8] | `CDLSSP1\0` |
| version | uint16 | 1 |
| message_type | uint16 | 见消息值 |
| flags | uint32 | 包标志 |
| request_id | uint64 | 请求/响应对应 |
| payload_bytes | uint64 | 随后 payload 大小 |

消息为 HELLO=1、CONFIGURE=2、CONFIGURED=3、FRAME=4、FRAME_RESULT=5、RESET=6、CANCEL=7、SHUTDOWN=8、ERROR=9。控制 payload 为紧凑 UTF-8 JSON；帧消息用下述二进制。flag 为 RESET_HISTORY=1、END_OF_STREAM=2。首帧、切镜、不连续或用户 reset 附 RESET_HISTORY，不可跨 reset 复用历史。

## 帧头（40 字节）

| 字段 | 类型 |
| --- | --- |
| input_width, input_height | uint32, uint32 |
| output_width, output_height | uint32, uint32 |
| pts_ns | int64 |
| frame_index | uint64 |
| plane_count | uint32 |
| reserved | uint32，必须 0 |

后接 plane_count 个描述，再按描述顺序接各平面字节，同一 semantic 最多一次。

## 平面描述（32 字节）

| 字段 | 类型 |
| --- | --- |
| semantic | uint16 |
| pixel_format | uint16 |
| width, height | uint32, uint32 |
| row_pitch | uint32 |
| flags | uint32，v1 必须 0 |
| reserved | uint32，必须 0 |
| byte_count | uint64，必须 row_pitch × height |

语义 COLOR=1、MOTION=2、DEPTH=3、EXPOSURE=4、REACTIVE_MASK=5、OUTPUT_COLOR=100。格式 RGBA8_UNORM=1、RGBA16_FLOAT=2、RG16_FLOAT=3、R32_FLOAT=4、R8_UNORM=5。

允许 padding；row_pitch 至少 width×每像素字节数。颜色 RGBA8/FP16；RGBA8→linear FP16 属未来渲染路线，当前诊断不转换。引导可选以独立演进，不代表当前 NR 消费。

| 语义 | 格式 | 尺寸 |
| --- | --- | --- |
| COLOR（必选） | RGBA8_UNORM / RGBA16_FLOAT | 输入尺寸 |
| MOTION | RG16_FLOAT | 输入尺寸 |
| DEPTH | R32_FLOAT | 输入尺寸 |
| REACTIVE_MASK | R8_UNORM | 输入尺寸 |
| EXPOSURE | R32_FLOAT | 1×1 |

OUTPUT_COLOR 只用于响应。返回颜色和所有引导均**经 GPU 回读**，不复用 CPU 输入；只保有效像素，丢 padding，拒绝缩放。R32_FLOAT depth 只是可拷贝数据纹理，不是已验证 NGX typeless/DSV 绑定。拷贝不证明运动尺度、深度、曝光或历史语义，渲染器须验证自身契约。

本诊断 HELLO 返回 configuration_supported:false，CONFIGURE accepted:false 并给原因；RESET 确认无状态重置。拷贝成功不代表应用效果。

## 进程生命周期

一个 Worker 拥有一个运行时，仅指纹一致时可处理多帧/任务；DLL/后端改变须关闭后重建，不热切。Python 父进程拥有超时和崩溃控制；可响应时发 CANCEL，但仍须能终止隔离进程。只有匹配 FRAME_RESULT 才接受帧；不完整或未经验证的输出不能替换用户目标文件。
