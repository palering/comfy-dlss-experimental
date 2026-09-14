# 自有 caller shim（仅供开发）

[English](caller-shim.en.md) · 简体中文

Audience: public

`sidecar/src/nr_caller.cpp` 实现一个无状态的薄转发 DLL。它**不是当前视频 Worker**，
也不是模型，不能直接替换 Video Converter 名为 `nvngx.dll` 的可执行程序或其他项目
的 caller 接口。旧预设不会选择它；实验 [owned_nr 运行时](owned-runtime.zh-CN.md)会选择它，
helper 打包可用 `--include-owned` 显式包含它。不代表已有二进制发布包或兼容其他 caller ABI。

## 职责

宿主提供类型正确的函数指针，拥有模块生命周期、参数存储、设备／队列／资源状态、
同步、Feature 生命周期、模型选择与任务传输。shim 不保留这些状态。它原样返回调用
结果；目标函数为空时返回 InvalidParameter，不调用函数、不修改输出存储。
导出不修改 DLL 字节、不解析符号、不创建线程、不加载模型、不实现 Parameter 虚表。

| 导出 | 契约 |
| --- | --- |
| `ComfyNR_CallerAbiVersion` | 返回本项目 caller ABI 版本 1。 |
| `ComfyNR_CallInitParameters` | 最后两个参数为 `version, const Parameter*` 的 Init。 |
| `ComfyNR_CallInitFeatureInfo` | 独立 Init ABI，末尾为 `const FeatureCommonInfo*, version`。 |
| `ComfyNR_CallCreate` | Snippet CreateFeature 契约，参数对象为 const 输入。 |
| `ComfyNR_CallEvaluate` | 带 handle、参数与可选进度回调的 EvaluateFeature。 |
| `ComfyNR_CallRelease` | 释放传入的 Feature handle。 |
| `ComfyNR_CallShutdown` | 无参旧版 Shutdown，不是 Shutdown1(device)。 |

核心类型与调用签名来自官方 SDK 声明。两种 Init 布局刻意分离：仅凭模型文件名或导出
名称，无法确定它使用哪种 ABI。后端必须先核实具体运行库契约再绑定，不实现启发式
自动猜测。shim 也不会让未知 NR 参数键或模型私有 ABI 变成官方接口。

转发函数禁止内联，调用后保留结果操作，构建关闭尾调用优化和 LTO，使返回地址保留
在 shim 内而不是被优化成跳转。但这**不能证明**模型的调用者验证一定接受它。
目标函数与回调不得跨 C 接口抛出异常；编译检查不等于运行时对象生命周期已验证。

## 构建与验证

仅开发者需要单独准备官方 NVIDIA DLSS SDK 头文件和已有 Zig。头文件不复制到仓库。
不链接 NGX 静态库，不需要 Windows/MSVC 主机或 llvm-mingw。

```bash
NGX_SDK_INCLUDE=/path/to/NVIDIA-DLSS/include bash sidecar/build_caller.sh
NGX_SDK_INCLUDE=/path/to/NVIDIA-DLSS/include bash sidecar/build_caller_test.sh
```

第一条生成 Windows x86-64 DLL：`sidecar/build/caller/nvngx.dll`。
第二条使用宿主 `clang++`（或 `CXX`）对假目标做参数、返回值、回调与空函数指针测试，
启用 AddressSanitizer／UndefinedBehaviorSanitizer，不加载模型、不使用 GPU。
构建、编译缓存与测试产物均位于被忽略的 `sidecar/build/`，不作为源码上传，也不要
拿它覆盖用户已有 DLL。

本地已验证 sanitizer 转发测试、PE 导出与无尾调用反汇编。实际模型的
Init/Create/Evaluate 接受情况、Windows/Proton 执行，以及完整自有视频 Worker，
**不因这个组件完成而视为完成**。参见[运行时角色](RUNTIME_ROLES.zh-CN.md)与
[C++ 质量约束](sidecar-cpp-quality.zh-CN.md)。

独立的[自有验证宿主](owned-worker.zh-CN.md)为实机测试提供参数存储与 D3D12 生命周期，
这些职责不会移入 shim。
