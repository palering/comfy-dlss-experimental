# 安装、双平台执行与辅助程序分发

Audience: public

状态：2026-09-03。[公开仓库](https://github.com/palering/comfy-dlss-experimental)
首发提供源码，不提交编译产物。辅助程序的 Release ZIP 安装机制已实现，
但目前尚未发布预编译资产；可自行构建或等待后续经过平台验收的包。
不会自动下载外部 NR Worker、模型 DLL、Proton、驱动或 CUDA SDK。

## 用户安装

1. clone 到 `ComfyUI/custom_nodes/comfy-dlss-experimental`。
2. 使用 **ComfyUI 自己的 Python** 检查 numpy、支持 DIS 的 cv2、av、PIL；宿主 PATH
   中须有 ffmpeg、ffprobe。当前 requirements.txt 只是说明，不会自动补齐依赖。
   安装脚本不修改 Comfy 的 PyTorch/CUDA 环境。
3. 获取本项目 `comfy-dlss-helpers-<version>-<target>.zip` 和可信 SHA-256。
   target 为 linux-x86_64 或 windows-x86_64；macOS/ARM 尚不支持。
4. 在节点根目录执行，python 换成 ComfyUI 的解释器（portable 用其 python_embeded）：

   ```sh
   python install.py --archive /path/to/helpers.zip --sha256 <64位SHA256>
   ```

   或明确指定精确版本的 HTTPS 下载地址，不自动下载 latest：

   ```sh
   python install.py --url https://<发布地址>/<版本包>.zip --sha256 <可信校验值>
   ```

5. 自行准备可信、匹配的 `nvngx.dll` Worker 和 `nvngx_dlssnr.dll`。复制
   examples/runtime-presets/direct-nr.example.json 到节点数据目录
   runtime-presets/default.json，修改 Worker/model 路径。相对路径以该 JSON 目录为
   基准；`relay: "@bundled/relay"` 自动定位本机安装的中继。
6. 重启 ComfyUI，导入示例工作流，设置输入与 Runtime，先检查环境并渲染一帧。

安装仅写入节点 `sidecar/bin/<target>/<version>/` 与 `active.json` 指针。检查 ZIP
哈希、内部文件哈希、目标平台、文件大小和白名单；拒绝目录穿越、符号链接、重复
成员或外部 DLL。不会覆盖同版本不同内容，也不会删除旧版本。更新前停止 DLSS
任务，更新后重启 ComfyUI。可信哈希不能证明不可信发布者的文件安全。
节点管理器若无参数调用 install.py，只显示安装指引并正常退出，不会自动联网。

## 组件边界

| 组件 | Linux | Windows | 来源 |
| --- | --- | --- | --- |
| 节点 Python、前端 | 宿主运行 | 宿主运行 | 源码仓库 |
| dlss-native-relay.exe | Proton | 原生 | 本项目包，同一个 PE |
| dlss-nvof-helper / .exe | 原生 ELF | 原生 PE | 本项目分别编译 |
| nvngx.dll Worker | 中继启动 | 中继启动 | 用户提供 |
| nvngx_dlssnr.dll | Worker 加载 | Worker 加载 | 用户提供 |

此处 nvngx.dll 是外部 Windows Worker 可执行程序，不是驱动的同名 NGX 库。只换
任意 shim 或模型不能保证符合 D5V2 协议。外部运行时、缓存和日志仍放用户指定的
数据目录，不写系统目录。安装包、编译输出、CUDA 构建头文件被 Git 忽略。
开发环境未安装包时保留 sidecar/build 查找；已安装包校验失败不会静默回退。

## Runtime 设置

- `系统平台` auto/windows/linux：auto 使用 Comfy 后端宿主，不看浏览器系统。
  手动项可用于编辑工作流，但执行必须匹配真实宿主；不是远程主机选择器。
- Windows：原生执行，隐藏并忽略 Proton 和 Linux 显示设置；不执行 Proton
  发现、Xwayland 扫描、fcntl 或 /proc 光流探测。
- Linux：`Linux 执行方式` 可选 proton 或 native_reserved。后者明确报未实现，
  不会偷偷回退或直接执行 PE。它与模型后端 nvidia_official 是不同扩展维度。
- `Proton 自定义路径` 优先于下拉，支持绝对安装目录或其中的 proton 文件、~。
  不接受附加命令，不经 shell。无效路径就报错，不换用其他版本。
- Linux/Proton 当前仍需 Steam 客户端目录和有效 Xwayland DISPLAY。建议较新稳定
  驱动；已测 RTX 4070 Ti SUPER、610.57.04、GE-Proton 11-6，不是最低版本承诺。

旧 widget 顺序不变，新输入追加默认值；隐藏项仍序列化，保留跨平台配置。
执行不信任旧 catalog 携带的宿主信息。Windows 使用 TEMP/TMP；Linux 使用
TMPDIR/XDG_CACHE_HOME。关闭 Linux 设置不是仅隐藏 UI，后端也隔离对应逻辑。

## 光流与验证边界

共用 CUDA Optical Flow 接口和运动场协议。Linux 加载 libcuda.so.1 和
libnvidia-opticalflow.so.1；Windows 仅从系统目录加载 nvcuda.dll 和 nvofapi64.dll，
采用二进制标准流和有界线程管道，不对匿名管道使用 POSIX selector。
驱动库只进入 helper，不加载到 Comfy Python。
[NVIDIA 官方接口说明](https://docs.nvidia.com/video-technologies/optical-flow-sdk/nvofa-programming-guide/index.html)。

Windows helper 已交叉编译，管道背压、日志洪泛、二进制数据、取消和超时有通用
测试；**不等于 Windows GPU 验收**。Windows NR、NVOF、常驻释放和中文路径仍需
实机测试。进程级资源统计仍仅 Linux 可用，Windows 不会以整卡数值冒充进程数据。

## 维护者构建

仅维护者需要已有 Zig、CUDA 头文件，不需要 nvcc 或编写 CUDA kernel。

```sh
python sidecar/build_nvof.py --target linux-x86_64 --cuda-include /path/to/cuda/include
python sidecar/build_nvof.py --target windows-x86_64 --cuda-include /path/to/cuda/include
bash sidecar/build_relay.sh
python scripts/package_helpers.py --target linux-x86_64 --version <version>
python scripts/package_helpers.py --target windows-x86_64 --version <version>
```

Linux glibc 目标 2.28。dist 输出两个 ZIP 和各自 .zip.sha256，包含我们编译的两个
helper、版本/文件哈希清单及 NVOF 头文件要求保留的声明。不收录 mock Worker 或
用户 DLL。打包不会自动发布到 GitHub；源码采用 MIT，附带声明另见根目录
THIRD_PARTY_NOTICES.md。发布 helper 资产前还需检查编译路径信息及完成目标平台验收。
CI 增加 Windows/Linux Python 测试矩阵，GPU 测试仍是显式 opt-in。

## 验证范围

Linux 实机已验证单帧 NR、NVOF、安装版 helper、自定义 Proton 路径及宿主平台
拒绝逻辑。Python 与前端测试覆盖协议、安装、参数和平台条件显示；这些测试不
等于所有界面、长视频或跨 GPU 验收。Windows NVIDIA 实机验证仍未完成。
