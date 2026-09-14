# Sidecar C++ 质量规则

[English](sidecar-cpp-quality.en.md) · 简体中文

Audience: public

Windows sidecar 是 D3D12/NGX 周围的狭窄原生边界，以 Zig 编译为 Windows x86-64 PE，原生或通过 Proton 运行。以下规则适用于所有 sidecar 修改。

## ABI 与未定义行为

- 不猜测 NGX 函数声明。调用前依据权威 NVIDIA 头文件匹配函数类型、结构布局、调用约定、枚举宽度及 SDK 版本。
- 显式解析可选 DLL 导出，缺少符号作为能力错误。不要把任意数据指针强转为函数指针。
- Windows ABI 声明集中在适配层；不跨协议暴露 SDK 指针，不跨不兼容运行时代际保留指针。
- 分配或拷贝前验证尺寸、pitch、格式、偏移、计数及整数转换。
- 开启警告并保持无警告；无专有 DLL 的代码路径应提供 sanitizer 构建。

- Caller 转发使用独立声明的 ABI／版本，区分不同 Init 参数布局，保留 shim 内的真实返回地址，并检查优化后的导出代码。不能用薄 DLL 替换旧视频可执行程序。

## 所有权与生命周期

- HMODULE、COM 接口、Win32 handle、文件、内存、D3D12 资源、fence、NGX feature 均使用 RAII。
- 所有权包装器不可复制；移动后源对象为空。
- NGX feature 在 shutdown 前释放，GPU 资源在设备前释放，NGX 模块最后释放。
- 借用指针不得超过所有者约定的生命周期。
- 失败后对象必须仍可安全析构。例外：GPU 提交后无法确认完成时，隔离 Worker 必须直接终止，不展开可能仍在飞行的资源所有者。当前拷贝层记录数字 fence 错误并调用 `std::_Exit(70)`；fence/event/输出存储在提交前分配，提交到完成确认之间不做可能抛异常的分配。

## 错误与进程隔离

- 检查所有 HRESULT、Win32/NGX 返回码、文件操作和协议解析；机器可读错误含操作及稳定数字代码。
- 设备移除、ABI 不匹配、初始化失败或 GPU 同步不完整后不得继续。
- NVIDIA/注入 DLL 不进入 Comfy Python。sidecar 崩溃只令该任务失败。
- 限制输入大小、日志、执行时间及来自 Python 的路径。

## 构建与验证

- 使用仓库脚本和已有 Zig；没有已证实阻塞时不引入第二套交叉工具链。
- 分别测试加载失败、缺少导出、非法输入、输出文件失败，以及成功 Proton/D3D12 初始化。
- 不将 NVIDIA、ReShade、RenoDX、Proton 二进制打入程序，它们仍由用户选择。

## GPU 布局与同步参考

有效行字节与 D3D12 对齐 row pitch 不同；用返回的 footprint，拷贝前检查每个访问区间。资源、allocator 和命令列表须存活到 fence 完成确认。

- [Microsoft：GetCopyableFootprints](https://learn.microsoft.com/en-us/windows/win32/api/d3d12/nf-d3d12-id3d12device-getcopyablefootprints)
- [Microsoft：基于 fence 的资源管理](https://learn.microsoft.com/en-us/windows/win32/direct3d12/fence-based-resource-management)
