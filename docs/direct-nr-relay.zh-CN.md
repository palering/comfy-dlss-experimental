# Direct Feature 18：外部 Worker 隔离适配器

[English](direct-nr-relay.en.md) · 简体中文

Audience: public

已接入 Comfy 视频处理与节点卡片预览，见[节点说明](comfy-video-nodes.zh-CN.md)。NGX 调用成功不等于感知质量或时序稳定。[真实视频验证](video-nr-validation.zh-CN.md)覆盖区间准备、运动引导与匹配导出。

## 执行与构建边界

```text
宿主 Python（媒体 / 参数 / 监管）
  → 已认证回环 TCP 临时端口（CLR1）
  → dlss-native-relay.exe（本项目 C++，已有 Zig 编译）
  → Windows stdin/stdout/stderr 匿名管道
  → nvngx.dll --video（用户提供的原始 Worker 可执行程序）
  → nvngx_dlssnr.dll（匹配模型/运行时）
  → RGBA8 结果沿原路返回
```

Relay 不渲染，也不是 NGX caller shim。外部程序保留 nvngx.dll 文件名及自身 Feature 18 实现；不把它改名为 relay，也不另加 caller/nvngx.dll。已验证 RTX40 converter v0.1.0 配套包。

不需要 converter 网页服务、Windows nvidia-smi.exe、ReShade、RenoDX、Streamline、额外 SDK 静态库或 Windows 构建机。GPU 预检在宿主原生侧，真正的 create/evaluate 是兼容验证。ReShade 另保留研究路线。

build_relay.sh 生成 sidecar/build/dlss-native-relay.exe 与 mock-nr-worker.exe，后者无 GPU、仅测试传输/故障，不能代替 DLSS。光流另行构建。不分发外部 Worker/模型；它们执行原生代码，独立进程/prefix 不是安全沙箱，应记录精确哈希。

## 协议与所有权

Relay 连接 127.0.0.1，以每次运行 32 字节 token 认证，收到 ACK 才启动子进程。CLR1 头部 12 字节小端（magic 0x31524C43、kind、payload 大小），payload 上限 65,536 字节。通道含 stdin、EOF、cancel、stdout、stderr、exit、error、启动 PID，与旧 [frame-stream-v1](../sidecar/protocol/frame-stream-v1.zh-CN.md) 独立。

direct_nr.py 实现所选外部 Worker 的 D5V2：

| 消息 | 编码 | 内容 |
| --- | --- | --- |
| 视频头 | <10I4f>，56 字节，0x32563544 | 尺寸、warmup、帧数、整数与浮点控制 |
| 输入帧 | <4Iq>，24 字节，0x314D5246 | index/reset/reserved=0/时间戳，随后颜色与运动 |
| 输出帧 | <5Iq>，28 字节，0x3154554F | index/成功/字节数/NGX 结果/时间戳，随后颜色 |

两输入平面均 width×height×4 字节，颜色 RGBA8、运动 RG16F。检查大小、索引、时间戳、成功码、结束退出和额外尾部；失败流不能复用。无 depth/exposure/reactive mask 输入。最初 smoke 是零运动静止图，后续运动/真实素材证据另见视频验证。

隔离模式每次启动 relay/Worker。Windows 使用 kill-on-close Job Object、挂起创建后归组、显式继承 handle 白名单、有界包和独立输出/日志线程。Python stdout 缓冲上限 256 MiB，保留 Worker 日志 4 MiB，超过仍持续排空；这不限制 Proton launcher/debug 的独立日志。

取消先控制通道、再关闭 socket；子进程管道阻塞可延迟 relay 响应。Linux 用随机环境标记、同用户检查与 pidfd 精确终止所属进程（含脱离的 Proton 子进程），残留即清理失败，禁止通用名称 kill。prefix 排他锁防并用，清理失败保留锁直到重试成功；需 pidfd 和可读同用户 /proc。

默认隔离，可选常驻为已验证哈希对保持有界 D5V2 流，任务 reset 不重复模型初始预热。改 DLL/尺寸/NR 参数新建实例，不支持热换 DLL/实时参数编辑，见[常驻](preview-performance.zh-CN.md#opt-in-resident-worker)。Windows 原生路径已有，GPU 实机验收未完成。

## 构建与测试

```bash
bash sidecar/build_relay.sh
python3 -m unittest discover -s tests -v
c++ -std=c++20 -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer -I sidecar/src \
  sidecar/tests/relay_protocol_test.cpp -o sidecar/build/relay-protocol-test
sidecar/build/relay-protocol-test
```

不需要 NVIDIA 头文件/静态库；遵守 [C++ 规则](sidecar-cpp-quality.zh-CN.md)。可移植 sanitizer 不等于 Windows/Proton 已跑 sanitizer。

Linux 使用 Comfy 同级专用测试目录，不用系统目录或共享安装位置：

```text
dlss/direct-nr-experimental/
  bin/                 # relay、mock
  runtime/             # 用户 Worker、模型
  code/                # Python、脚本、测试
  prefix-mock/         # 无 GPU 测试
  prefix-direct/       # 真运行时专用
  cache/
  tmp/
  mock/<run-id>/       # 报告和诊断
  direct/<run-id>/     # 报告、日志、帧
```

选择真实已安装 Proton 和当前会话 DISPLAY。诊断使用 Xwayland，不切换 Wayland 桌面，也不验证原生 Wayland。

```bash
python3 scripts/run_direct_nr_smoke.py \
  --relay /path/to/dlss/direct-nr-experimental/bin/dlss-native-relay.exe \
  --worker /path/to/dlss/direct-nr-experimental/bin/mock-nr-worker.exe \
  --proton '/path/to/installed/Proton/proton' \
  --root /path/to/dlss/direct-nr-experimental --mock-suite --timeout 30

python3 scripts/run_direct_nr_smoke.py \
  --relay /path/to/dlss/direct-nr-experimental/bin/dlss-native-relay.exe \
  --worker /path/to/dlss/direct-nr-experimental/runtime/nvngx.dll \
  --proton '/path/to/installed/Proton/proton' \
  --root /path/to/dlss/direct-nr-experimental \
  --timeout 300 --warmup 120 --frames 8 --intensity 1.0
```

smoke launcher 假定 Steam 位于 ~/.local/share/Steam；不是通用运行时发现 UI。首次 prefix 较慢，不可指向游戏/其他运行中程序的 prefix。

## 已验证证据与限制

RTX 4070 Ti SUPER、Linux 610.57.04、GE-Proton 11-6、Wayland + Xwayland，RTX40 配套指纹：

- Worker SHA-256：`99ef1f2976d9cd16b7fc269adb6c6450fb64c81a522c9b9e6edc6a28201dc904`
- Model SHA-256：`28bdc080d28686decdb63f6f4246b022274916b80aafdab266fe0fb63b2b9265`

macOS C++ 协议 ASan/UBSan 通过。无 GPU Proton 的字节往返、日志洪泛、早退、截断、NGX 错误传递、取消、断开、超时、阻塞输入取消 9 项通过，所属残留 0；这些不调用 NGX。

640×360 静止图 Feature 18 创建返回 0x00000001，120 次预热+8 输出全部成功，正常退出，结果不同于输入。intensity 0.25/1.0 哈希不同，index 2 reset 在该样例重现初始序列。复核无 relay/Worker，显存回到 3 MiB 空闲基线；约 3 秒 smoke 不是视频吞吐基准。120 是历史测试配置，不要求每次预览重复；History 控制预热/上下文，常驻可复用。

只证明所选版本组合能跑，非全版本、自然图像质量、长视频稳定、光流正确或原生 Wayland 的证明。Comfy 预览/导出后续独立验证；其他头字段须验证真实响应。现已有片段/切镜/引导、预设、卡片预览、输出和输入诊断，DLL 选择、准备、效果与进程策略保持分离。
