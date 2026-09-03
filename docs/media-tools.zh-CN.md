# FFmpeg、ffprobe 与 PyAV

[English](media-tools.en.md) · 简体中文

Audience: public

本节点除了 Python 媒体依赖，还需要**宿主 FFmpeg 和 ffprobe 可执行程序**。安装 ComfyUI 或 PyAV 不代表这两个命令一定存在。

## 各组件的作用

| 组件 | 用途 |
| --- | --- |
| PyAV（`av`） | FFmpeg 库的 Python 绑定，用于 Comfy 原生 VIDEO 及本项目解码/实体化 |
| `ffprobe` 命令 | 检查输入元数据、验证输出标签与实际帧数 |
| `ffmpeg` 命令 | 宿主视频/音频导出、编码及色彩滤镜链 |
| Proton / NR Worker | 神经渲染；不提供或启动宿主媒体命令 |

Comfy 的[原生视频节点](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_video.py)和 [VIDEO 实现](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_api/latest/_input_impl/video_types.py)使用 PyAV。[PyAV 安装文档](https://pyav.org/docs/stable/overview/installation.html)说明 wheel 关联 FFmpeg 库，但这不保证同时安装独立 ffmpeg/ffprobe 命令。第三方整合包可能附带，需以实际后端检查结果为准。

## Input Adapter 可选路径

新增两个高级字符串输入，追加在旧字段之后，不改变原工作流参数位置：

- `ffmpeg_path`：可执行文件绝对路径，或**存放程序的目录**。留空使用 Comfy 后端 PATH。
- `ffprobe_path`：可独立指定文件/bin 目录。留空且已指定 FFmpeg 时使用所选目录中的 ffprobe；两项都留空则分别从后端 PATH 查找。

Linux 示例：`/usr/bin` 或 `/opt/ffmpeg/bin/ffmpeg`。
Windows 示例：`D:\Tools\ffmpeg\bin` 或 `D:\Tools\ffmpeg\bin\ffmpeg.exe`。
路径属于 **Comfy 服务器**，不是浏览器电脑、Proton prefix 或远程 URL。支持宿主的 ~ 展开及空格，不要添加引号、shell 命令或参数。

显式路径无效或同目录缺少 ffprobe 时明确报错，不静默换成 PATH 中另一版本。必须是可执行文件，并通过 `-version` 自报程序身份；版本检查超时为 5 秒，按路径/大小/修改时间缓存。只使用可信程序。

## 检查与执行

点击“检查输入”，节点内“媒体工具（宿主系统）”显示可用性、实际路径、选择来源、版本/错误和 PyAV 版本。这是**上次检查**，不是实时监控；登录 shell 的 PATH 可能与 Comfy 服务不同。

Preview / Process 会根据 sequence 配置重新解析，并在任务详情记录实际工具。输入检查、音视频导出及结果验证使用所选程序。配置只作用于该任务，不修改全局 PATH 或另一工作流。独立诊断 CLI 默认仍使用自身进程 PATH，除非其接口另有覆盖选项。

准备缓存版本 6 包含工具身份；更换工具使不兼容准备缓存及派生原始预览失效，仅改 Look 仍复用匹配缓存。文件身份使用路径/大小/mtime 与版本，**不是二进制加密完整性保证**。

## 缺少时再安装

从 [FFmpeg 官方下载页面](https://ffmpeg.org/download.html)进入，页面同时链接源码与各平台
软件包/预编译发行版。选择可信的宿主版本，确保包含 **ffmpeg 和 ffprobe 两个程序**，
保留旁置共享库与许可文件；已有可用版本就复用。
本节点不会代为下载、安装、改系统目录或 Comfy PyTorch/CUDA。指定独立 bin 目录即可使用。

**本节点不要求 ComfyUI 存在 tools 目录。** 便携程序可以放在用户自行选择的位置；
安装指南中的数据根 tools 布局只是可选示例，不是 Comfy 标准目录或自动搜索位置。

<a id="pip-installation"></a>

### 可选 pip 安装

[static-ffmpeg](https://pypi.org/project/static-ffmpeg/) 是第三方可选方案：pip 安装管理包，
初始化时才下载对应平台的两个程序，需要访问 PyPI 之外的下载源，不是 FFmpeg 官方发行包。
Linux uv 在 Comfy 根目录执行：

```sh
uv pip install --python .venv/bin/python static-ffmpeg
```

下面用 python 代表 Comfy 的解释器（Windows portable 在整合包根使用
python_embeded/python.exe）。pip 等效安装及显式下载/查询路径：

```sh
python -m pip install static-ffmpeg
python -c "from static_ffmpeg import run; print(*run.get_or_fetch_platform_executables_else_raise(), sep='\n')"
```

安装命令**二选一**，不要重复执行。将返回的两个路径分别填入 ffmpeg_path 和 ffprobe_path。
节点不会自动发现此包或触发下载，不需要全局调用 add_paths()；该发行版尚未完成本项目渲染/
编码能力验收。只装 ffmpeg-python 不会提供程序，imageio-ffmpeg 也不独自提供完整的两个程序。

当前导出需要实际调用的编码器/滤镜，包括 libx264、AAC、scale/format/setparams。`-version` 成功只说明程序可用，不保证每个编码器/滤镜或全视频解码正常。实际导出会检查尺寸、帧数、时间、色彩和音频，不支持的构建明确报错。

节点后端/schema 更新需重启 Comfy、刷新浏览器；日常只改路径值，重新检查和执行即可。刷新前先保存未保存的工作流。
