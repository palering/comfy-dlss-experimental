# 自有 Worker 验证宿主

[English](owned-worker.en.md) · 简体中文

Audience: public

`sidecar/src/nr_probe.cpp` 构建实验性 `comfy-dlss-worker.exe`，提供分开的有界验证模式。
它拥有 D3D12 资源，通过自有[薄 caller](caller-shim.zh-CN.md)调用用户提供的 NR 模型。
实验 [owned_nr 运行时](owned-runtime.zh-CN.md)已通过独立的 [CNR1 接口](owned-protocol.zh-CN.md)
接入 Comfy。它不实现 D5V2，不静默替换旧预设。Linux／Comfy 短样本验收通过；
长视频与 Windows GPU 验收仍待完成。下面保留此前各诊断模式说明。

## 组件

| 组件 | 职责 |
| --- | --- |
| `nr_probe.cpp` | 诊断模式和 CNR1 请求／会话循环。 |
| `nr_engine.*` | 硬件选择、本地 DLL 加载、Feature 生命周期、纹理／传输和 GPU fence。 |
| `nr_parameters.cpp` | 根据官方 Parameter 接口由编译器生成实现；最多 192 个键、受检转换与同步访问。 |
| `nr_parameters.h` | 宿主使用的不透明 C 接口；禁止从 MinGW 编译单元调用 Parameter 虚表。 |
| `nr_input_contract.hpp` | 明确设置颜色／运动／输出的整帧有效区域，并在 ABI 自检中通过 SDK 接口检查。 |
| `nr_parameter_memory.cpp` | 通过本项目 C 函数，在同一宿主运行时分配与释放。 |
| `nr_caller.cpp` | 无状态有类型转发，不管理视频或参数存储。 |
| `probe_report.hpp` | 带认证的本机连接、就绪握手、有序阶段报告和完成确认，I/O 有超时。 |
| `run_owned_probe.py` | Linux 无 GPU 验证启动器，使用 `proton run`，不依赖启动器退出码或标准输出。 |

重载的 Parameter 接口在 Microsoft 与 MinGW C++ ABI 下有不同的虚函数排列。
参数实现与独立虚调用测试使用已有 Zig 编译成 **Microsoft ABI 对象**；宿主仍使用
MinGW 目标，边界上只有 C 函数和不透明指针，不手写虚表、不代造 MSVC 运行时符号。

构建使用 Zig 自带的 Windows C 头文件与 `ntdllcrt` 导入库，从平台 `ntdll.dll`
取得浮点使用标记。不链接 NVIDIA SDK 静态库或 MSVC C++ 标准库，不下载 SDK，
不安装第二套编译器。Windows/Proton 加载兼容性仍须实机检查，交叉链接成功不代表
运行时兼容性已验证。

## 构建

单独准备官方 NVIDIA DLSS SDK 头文件：

```bash
python3 sidecar/build_owned_worker.py --ngx-include /path/to/NVIDIA-DLSS/include --native-tests
```

需要已有 `zig` 和宿主 `clang++`（或 `CXX`）；没有宿主 sanitizer 工具时可省略
`--native-tests`。`--zig` 与 `--zig-lib-dir` 可明确指定现有工具位置。
对象文件、程序、测试与默认编译缓存均位于被忽略的 `sidecar/build/`。

## 实机验证顺序

后续[连续帧／流式结果](owned-protocol.zh-CN.md#验证边界)扩展了下面的最初单帧证据。
CNS1 `--probe-sequence` 是独立诊断：最多 16 帧、64 MiB，使用 GPU 前预检查所有记录，
内存只保留一帧，独占创建输出文件；它不是视频缓存格式。

自有模型路径已在 Linux/GE-Proton、RTX 4070 Ti SUPER 上通过**一次 640×360 合成
单帧测试**：Init/Create/Evaluate、两次 GPU fence、有限 FP16 输出读回、Release 和
Shutdown 均成功。这不是视频或画质验收。先前较小尺寸实验出现过设备丢失；已补齐
缺失的运动有效区域参数，但测试尺寸和启动配置也发生了变化，因此尚未证明先前
故障的唯一根因。不要在安装期间自动执行 GPU 探测，也不要把程序允许输入的尺寸
范围当成模型已支持的范围。

Linux 优先使用下面的无 GPU 启动器，保持已有 `proton run` 路径，等待经过认证的
Worker 消息，而不是依据启动器退出码判定结果：

```bash
python3 sidecar/run_owned_probe.py \
  --worker /path/to/comfy-dlss-worker.exe \
  --proton /path/to/proton \
  --prefix /path/to/isolated-existing-compatdata \
  --steam /path/to/Steam \
  --output /path/to/new-attempt
```

prefix 必须已经初始化且专用于测试。输出目录必须尚不存在，父目录须存在。
启动器保留日志、持有 prefix 锁、限制总时长，结束后**只关闭该 prefix**。
不要指向游戏或日常 Comfy 使用的 prefix。`--mode report-failure` 是故意触发的
无 GPU 失败测试：Worker／脚本预期返回 1，即使 Proton 返回 0 也不能判成功。
这个 Python 启动器不开放模型／GPU 模式。

C++ 宿主可选接收 `--report PORT TOKEN`（由启动器生成的本机端口和 32 字节随机
令牌的十六进制文本）。诊断协议 1 使用有大小上限的 JSON 行，包含序号、阶段、
状态、错误码，以及连接认证确认和最终完成确认。连接模式下宿主不向控制台写
结果／错误，而通过 socket 上报，避免控制台处理阻塞完成路径；不控制第三方库
自行打印的日志。阶段报告区分 API 返回和 GPU 完成，特别是 `create_fence` 与
`evaluate_fence`。通道错误退出 74，不析构可能仍在 GPU 上使用的资源。
这只是诊断协议，不是视频传输协议。

Linux/Proton 还通过了就绪握手、全部 17 个参数虚函数及故意失败路径的无 GPU 验证。
Windows 原生运行仍未验收。

先在 Windows 或隔离的 Proton prefix 内运行不使用 GPU 的 ABI 检查：

```text
comfy-dlss-worker.exe --self-test-parameter-abi
```

它通过单独编译的 Microsoft ABI 消费者，检查官方全部 17 个 Set/Get/Reset 虚函数。
成功报告 `error_bits: 0`、`gpu_started: false`，不代表模型加载或渲染成功。

核对所选模型的 **InitParameters** ABI，并处理先前 GPU 失败原因后，应先分阶段
进行原生诊断，再进入评估：

| 模式 | 范围 |
| --- | --- |
| `--probe-device` | 仅创建设备并释放，不加载模型。 |
| `--probe-init-parameters MODEL CALLER LOG_DIR` | 设备、模型初始化、shutdown，不创建 Feature。 |
| `--probe-create-parameters MODEL CALLER LOG_DIR WIDTH HEIGHT` | 创建 Feature、提交并等待 fence、释放，不执行 Evaluate。 |
| `--probe-nr-init-parameters MODEL CALLER LOG_DIR WIDTH HEIGHT EVALS` | 完整有界探测，不是正式视频任务。 |

模型初始化与 Feature 创建内部也可能提交 GPU 工作。设备、初始化和创建已分别
验证通过，之后才进行了上述有界单帧评估。所有路径须为绝对路径，专用日志目录须预先存在。
下面仅说明语法，不表示建议重复运行已失败的 GPU 配置：

```text
comfy-dlss-worker.exe --probe-nr-init-parameters MODEL_ABS CALLER_ABS LOG_DIR_ABS 640 360 1
```

该模式使用合成 FP16 灰色图和零运动，不读取用户视频，执行
Init/Create/Evaluate/Release/Shutdown。任一时刻只有一次评估在执行，最多 240 次。
宽限制为 64–1920，高为 64–1080，复用资源；只读回最终输出、检查非有限值并计算校验和。
颜色、运动和输出都明确声明有效区域，当前模型会将未提供的运动区域宽高置为零。
GPU 等待有上限，无法确认完成时只终止本进程，不析构可能仍在使用的资源。
释放和 shutdown 都成功后才输出成功结果。

NR 探测另有 120 秒进程看门狗，覆盖模型初始化与清理，而不仅是 GPU fence 等待，
以限制外部函数挂起。不使用 GPU 的 ABI 单独检查命令仍应由测试启动器设置超时。

探测不会额外初始化官方 NGX core，也不会猜测模型私有参数或 ABI。如果模型要求更多
初始化或拒绝输入，记录操作及错误后停止，不在同一个原生状态上尝试不同函数签名。
不 patch、不覆盖任何 DLL。

本地测试涵盖表容量、数字转换、空分配、并发访问、sanitizer 生命周期检查与交叉编译。
Windows/Proton GPU 执行、模型接受情况、画质、视频传输、常驻和 SR/FG 都不能由这些本地
结果证明。报告明确将 `video_protocol`、`visual_acceptance` 保持为 false。
