# NVIDIA Optical Flow API headers

English · [简体中文](README.zh-CN.md)

Audience: public

Unmodified headers from NVIDIA/NVIDIAOpticalFlowSDK, commit
`edb50da3cf849840d680249aa6dbef248ebce2ca` (public API 2.0).
Source: https://github.com/NVIDIA/NVIDIAOpticalFlowSDK/tree/edb50da3cf849840d680249aa6dbef248ebce2ca

The redistribution notices are retained in each header. These are interface
declarations, not NVIDIA runtime binaries. CUDA headers and the GPU driver are
provided by the build/runtime host, not bundled here.

Do not mix these ABI structures with API 5.0 documentation declarations. The
helper deliberately uses this version's supported calls. In particular it uses
two sessions for forward/backward flow, not API 5.0's combined prediction call.
