# 安装、双平台执行与辅助程序分发

[English](distribution.en.md) · 简体中文

Audience: public

状态：2026-09-03。[公开仓库](https://github.com/palering/comfy-dlss-experimental)
首发提供源码，不提交编译产物。辅助程序的 Release ZIP 安装机制已实现，
但目前尚未发布预编译资产；可自行构建或等待后续经过平台验收的包。
不会自动下载外部 NR Worker、模型 DLL、Proton、驱动或 CUDA SDK。

## 用户安装：从 clone 到第一次检查

### 第一步：只把源码 clone 到 custom_nodes

在 ComfyUI 根目录执行：

```sh
git clone https://github.com/palering/comfy-dlss-experimental.git custom_nodes/comfy-dlss-experimental
```

不需要先把整个 Comfy 搬进其他环境，也不把 Video Converter 解压覆盖到节点目录。
源码、Python 环境、外部运行时与我们的构建产物是四种不同内容。

### 第二步：确认正在使用 Comfy 的 Python

Linux + uv/.venv，以下从 ComfyUI 根目录执行：

```sh
.venv/bin/python -c "import sys; print(sys.executable); print(sys.prefix)"
.venv/bin/python -c "import numpy, cv2, av, PIL; assert hasattr(cv2, 'DISOpticalFlow_create'); print(cv2.__file__); print(av.__file__)"
```

只有确认缺失对应包时才安装。例如完全没有 cv2：

```sh
uv pip install --python .venv/bin/python opencv-python-headless
```

缺 numpy、av 或 PIL 时分别安装 numpy、av、Pillow。使用已有环境约束，先看依赖解析计划；
不要无条件重装所有包或加 --upgrade。pip 可用时也可用
`.venv/bin/python -m pip install <缺少的包>`。

Windows portable 从**整合包根目录**运行：

```powershell
.\python_embeded\python.exe -c "import sys; print(sys.executable)"
.\python_embeded\python.exe -c "import numpy, cv2, av, PIL; assert hasattr(cv2, 'DISOpticalFlow_create'); print(cv2.__file__)"
# 仅当尚未安装任何提供 cv2 的包时：
.\python_embeded\python.exe -m pip install opencv-python-headless
```

Windows 普通 venv 使用 Comfy 的 .venv\Scripts\python.exe；
Conda/其他管理方式同样选择**启动 Comfy 的解释器**，不是浏览器机器或系统默认 Python。
不要因为 portable 的 python_embeded 拼写特殊而自行建第二个环境。

OpenCV 导入名是 cv2；其 wheel 安装到所选解释器的 site-packages（Windows 可能是
Lib/site-packages），不放 DLL 目录。已有 opencv-python / contrib / headless 版本能导入且
支持 DIS 时直接复用。本节点不需要为了 DIS 自动换 contrib 或 CUDA OpenCV。
[OpenCV 包说明](https://pypi.org/project/opencv-python-headless/)强调多个 wheel 共用 cv2 命名空间，
**不能同时装多个变体**。若已安装但缺功能/导入失败，先诊断，不再叠装一个 headless。
也不把 wheel 解压到节点 vendor 目录并临时改 sys.path，这会与 Comfy 及其他节点冲突。

当前 requirements.txt 仅说明依赖；`pip install -r requirements.txt` **不会补装媒体包**。
安装脚本也不会自动修改 PyTorch、CUDA、NumPy 或卸载现有 OpenCV。

### 第三步：检查或选择宿主 FFmpeg

已有 ffmpeg/ffprobe 可执行时直接复用。Linux 系统包常位于 /usr/bin；
Windows 可使用已有安装，也可下载便携发行版。无需为本节点强制改系统目录。

未安装又不想改系统时，从 [FFmpeg 官方下载入口](https://ffmpeg.org/download.html)
选择对应宿主的构建，解压**完整发行目录**（含所需共享库及声明）到下面的 tools 路径；
不要只抽出依赖旁置 DLL 的两个 EXE。官方页面区分源代码与外部预编译发行方，
本项目不把那些第三方构建声称为自行提供的官方二进制。

目录由用户自己选择，**ComfyUI 不要求存在 tools 目录**；下文布局只是可选示例，不是安装前提。
也可参考 [pip 安装方式](media-tools.zh-CN.md#pip-installation)：static-ffmpeg 的首次显式下载
以及节点内手动填写路径的步骤。

在 Input Adapter 高级 ffmpeg_path 填实际 bin 目录绝对路径；ffprobe 同目录可留空。
目前不会自动搜索此 tools 约定目录，仍需填路径；已有后端 PATH 则两项都留空。
Linux 不使用 converter 包里的 Windows ffmpeg.exe，也不通过 Proton 执行 FFmpeg。
安装 Python 包 ffmpeg/ffmpeg-python 不能替代这两个原生命令，详见[媒体工具](media-tools.zh-CN.md)。

### 第四步：安装本项目 helper

用户目标是**下载预编译包，不要求自己装 Zig/CUDA 开发环境**。
[本项目 Releases](https://github.com/palering/comfy-dlss-experimental/releases) 在 2026-09-03
核对时尚无资产，因此当前仍需维护者构建后提供可信本地包，或等待首次 helper Release；
不要将尚不存在的下载步骤写成已经一键可用。

未来/本地 helper 包名：

- `comfy-dlss-helpers-<version>-linux-x86_64.zip`
- `comfy-dlss-helpers-<version>-windows-x86_64.zip`

从节点根目录，用 Comfy 的解释器执行（这里 python 代表该解释器）：

```sh
python install.py --archive /path/to/helpers.zip --sha256 <可信64位SHA256>
```

已有明确可信的精确 Release 资产时，可用已实现的 HTTPS 下载入口：

```sh
python install.py --url https://<发布地址>/<版本包>.zip --sha256 <可信64位SHA256>
```

脚本按实际宿主选 linux-x86_64/windows-x86_64；不从浏览器判断。
安装到节点 `sidecar/bin/<target>/<version>/`，在 target 目录写 active.json 指针。
包内含 relay、对应平台 NVOF helper、manifest.json、THIRD_PARTY_NOTICES.txt。
DIS 可以不运行 NVOF；当前标准 helper 包仍包含两个程序。

校验 ZIP SHA-256、内部文件哈希、目标标签、大小和白名单，拒绝路径穿越、符号链接、
重复项或外部 DLL。目标格式检查在打包阶段做；标签/哈希不是 GPU 兼容证明。
同版本不同内容不覆盖，旧版本不删除。包来源不可信时有哈希也不等于安全。
更新前停 DLSS 任务，之后重启 Comfy；校验失败不静默回退开发版。
节点管理器无参数调用 install.py 时只显示说明，不联网或安装依赖。

### 第五步：放好外部 Worker/模型并创建预设

按[文件来源与精确包路径](DLL_PREPARATION.zh-CN.md)准备两文件，放 components/nr 的版本目录。
从 examples/runtime-presets/direct-nr.example.json 复制到**数据根**的
runtime-presets/default.json。示例相对路径现在与下面布局一致；
换版本目录时同步修改，保留 relay 的 @bundled/relay。

**别把外部 converter ZIP 交给 install.py**：它只接受我们的 helper 包，会拒绝外部 DLL。
现阶段外部运行时手动选择/复制，不从网页、Discord 或驱动目录自动搜刮。

### 第六步：检查后再渲染

重启 Comfy、保存已有工作流后刷新页面、导入[示例](../example_workflows/README.zh-CN.md)。
检查 Input Adapter 的媒体信息和实际工具路径；Runtime 选择宿主/预设，
Linux 再选择已安装 Proton、Steam/Xwayland。先渲染一帧，再短片段，最后完整导出。
元数据/哈希通过只是预检，不保证 Create/Evaluate 成功或视觉质量。

## 各类文件的推荐布局

```text
ComfyUI/
  .venv/                                      # Linux/普通 venv 的 Python 包
  custom_nodes/comfy-dlss-experimental/        # Git 源码
    install.py
    sidecar/
      build/                                  # 维护者构建输出，Git 忽略
      bin/<target>/
        active.json
        <helper-version>/
          dlss-native-relay.exe
          dlss-nvof-helper[.exe]
          manifest.json
          THIRD_PARTY_NOTICES.txt
  user/default/comfy-dlss-experimental/       # 默认节点数据根
    components/nr/converter-v0.1.0-rtx40/
      nvngx.dll
      nvngx_dlssnr.dll
    runtime-presets/default.json
    tools/ffmpeg/<host-target>/<version>/
      bin/ffmpeg[.exe]
      bin/ffprobe[.exe]
      ...                                     # 发行版其余文件/许可
    runtime-snapshots/                         # 自动生成，不手改
    prepared-clips/
    prefixes/                                 # Linux/Proton
    executions/
```

Windows portable 的 python_embeded 位于 ComfyUI 目录的同级（整合包根），不是上图 .venv。
COMFY_DLSS_HOME 或 Comfy --user-directory 会改变数据根。
components/tools 的放置是推荐约定，不是新自动发现机制；现有自定义路径仍支持。
不自动搬迁已安装软件、旧 DLL 或用户缓存。

## 简化安装：已实现与建议下一步

| 操作 | 当前状态 | 简化方向（未实现） |
| --- | --- | --- |
| helper ZIP/HTTPS + hash 安装 | install.py 已实现；公共资产尚未发布 | 先发布经过检查的两平台资产，用户免编译 |
| Python 包检查 | 现用解释器命令/节点导入检查 | 增加只读 doctor，列路径/版本/DIS 能力和冲突 |
| Python 包安装 | 用户在 Comfy 环境显式补缺 | 明确同意后仅补缺，预览解析变更，不自动换 OpenCV/升级 NumPy |
| FFmpeg 选择 | PATH 或 Input Adapter 绝对路径，已校验/显示 | 可增加版本化便携包安装，下载源/哈希先核对；保留完整发行目录 |
| 外部 Worker/模型 | 手动文件 + 预设，执行时哈希/快照 | 可增加“导入本地运行时”：校验两文件、版本化复制、生成预设，不联网找 DLL |
| 驱动/Proton | 使用用户已有安装 | doctor 给指引，不自动安装驱动、修改系统或游戏 prefix |

建议统一的 setup/doctor 入口先**只读检查并显示计划**，用户确认后分别执行允许的操作；
不把所有东西塞进一个 vendor 文件夹，也不在节点 import 时装包。
表中的建议不是现有 CLI 参数，本次只完善说明与预设示例，没有实现全自动安装器。

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

预编译 Release 发布前，用户自行从源码构建也使用以下步骤。
只构建**本项目 relay 和可选 NVOF helper**，不构建外部视频 Worker 或专有 NR 模型。
以下均在节点仓库根目录执行。

### 1. 准备构建工具

- Python 3.11+ 用于 NVOF 构建/打包脚本；安装及媒体测试使用 Comfy 解释器。
  不需要另外安装 Python 构建包。
- PATH 中可用的 Zig，具备 C++20 与交叉目标支持；脚本调用 zig c++。
- build_relay.sh 需要 Bash。Windows 可使用已有 Git Bash 等环境，将 Zig 加入该环境 PATH；
  也可在其他系统交叉编译后复制产物。Bash 只是构建工具，不是 Windows 执行 NR 的要求。
- **仅 NVIDIA 光流需要**已有 NVIDIA CUDA API 头文件，含 cuda.h 及其包含文件。
  Optical Flow API 头文件已经在 sidecar/vendor/nvof 中。
  这里不需要 nvcc、CUDA kernel 编译、MSVC 主机或 NGX SDK。

构建脚本不会下载/安装编译器或 SDK。编译不需要 NVIDIA 显卡，执行 NR/NVOF 才需要。
macOS 可用于交叉构建，不是 NR 执行目标。Windows GPU 实机验收仍待完成。

### 2. DIS 只构建 relay

```sh
bash sidecar/build_relay.sh
```

sidecar/build 下生成：

- `dlss-native-relay.exe`：Windows x64 PE，可在 Windows 原生或 Linux/Proton 下运行。
- `mock-nr-worker.exe`：无需 GPU 的传输测试替身，不实现 NR，也不分发。

这一步不需要 CUDA 头文件。预设保留 @bundled/relay：新安装尚无
`sidecar/bin/<target>/active.json` 时，会直接解析到该构建输出。
配好外部两文件后即可测试 DIS；已有安装清单优先，仅重编 sidecar/build 不会替换已安装版，
更新已安装 helper 应使用新的版本化安装包。

### 3. 可选：构建 NVIDIA 光流

目标选 **Comfy 后端宿主**，与构建机器/浏览器平台无关：

```sh
python sidecar/build_nvof.py --target linux-x86_64 --cuda-include /path/to/cuda/include
python sidecar/build_nvof.py --target windows-x86_64 --cuda-include /path/to/cuda/include
```

只运行所需目标，分发时也可编译两份。输出分别是 sidecar/build/dlss-nvof-helper
（ELF，Linux glibc 2.28 目标）及 sidecar/build/dlss-nvof-helper.exe（Windows PE）。
helper 动态加载宿主驱动接口；CUDA/Optical Flow 驱动库不会打包。

### 4. 打包并安装本地构建

标准包要求**同目标的 relay 和 NVOF 两个 helper 都已构建**，即使最终工作流只用 DIS。
只构建 relay 时用上文开发输出查找方式，不要制作残缺 ZIP。
例如 Linux 包的两个 helper 都已生成后：

```sh
python scripts/package_helpers.py --target linux-x86_64 --version local-1
```

生成 dist/comfy-dlss-helpers-local-1-linux-x86_64.zip 和同名 .zip.sha256。
Windows 改选 windows-x86_64，产物文件名对应改变。local-1 只是本地构建标签示例，
不代表已发布版本；代码变化时换新标签，打包不会覆盖已有归档。

读取校验和文件。在**目标 Linux/Windows 主机**使用 Comfy 的 Python，
把下面哈希占位符替换为其中的 64 位值后安装：

```sh
python install.py --archive dist/comfy-dlss-helpers-local-1-linux-x86_64.zip --sha256 <SHA256_FROM_CHECKSUM_FILE>
```

Windows 使用对应 Windows 包名。更新前停止 DLSS 任务；脚本写入
`sidecar/bin/<target>/<version>/` 和 active.json，安装后重启 Comfy。
交叉编译时先把 ZIP/校验和复制到目标机；不要在 macOS 上运行安装器来安装 Linux 目标。

包内仅含本项目两个 helper、版本/哈希清单及 NVOF 头文件要求保留的声明。
不收录 mock Worker、外部 DLL 或 CUDA 头文件；build/、bin/、dist/ 产物由 Git 忽略。
打包不自动发布到 GitHub；原创源码 MIT，见[第三方说明](../THIRD_PARTY_NOTICES.md)。

分享产物前检查嵌入的构建路径、执行[开发检查](DEVELOPMENT.zh-CN.md#可移植验证)，
并完成目标 GPU 验收。Windows/Linux CI 测试及交叉编译通过不代表 Windows NR/NVOF
实机通过；GPU 测试仅在显式 opt-in 后运行。

## 验证范围

Linux 实机已验证单帧 NR、NVOF、安装版 helper、自定义 Proton 路径及宿主平台
拒绝逻辑。Python 与前端测试覆盖协议、安装、参数和平台条件显示；这些测试不
等于所有界面、长视频或跨 GPU 验收。Windows NVIDIA 实机验证仍未完成。
