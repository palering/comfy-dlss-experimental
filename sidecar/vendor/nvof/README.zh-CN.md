# NVIDIA Optical Flow API 头文件

[English](README.en.md) · 简体中文

Audience: public

来自 NVIDIA/NVIDIAOpticalFlowSDK 的未修改头文件，提交 `edb50da3cf849840d680249aa6dbef248ebce2ca`（公开 API 2.0）。
[来源](https://github.com/NVIDIA/NVIDIAOpticalFlowSDK/tree/edb50da3cf849840d680249aa6dbef248ebce2ca)。

每份头文件保留其分发声明。这些只是接口声明，不是 NVIDIA 运行库。CUDA 头文件和 GPU 驱动由构建/运行宿主提供，不在此打包。

不要混用 API 5.0 文档中的 ABI 结构。helper 刻意使用本版本支持的调用，前后向光流使用两个会话，而非 API 5.0 合并预测接口。
