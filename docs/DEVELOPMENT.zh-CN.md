# 开发与部署

[English](DEVELOPMENT.en.md) · 简体中文

Audience: public

仓库可直接 clone 到 `ComfyUI/custom_nodes/comfy-dlss-experimental`。用户安装见[平台与 helper 分发](distribution.zh-CN.md)。

## 可移植验证

在节点仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
node --test tests/*.mjs
python3 scripts/check_publication.py
```

维护完整中英文翻译并保留下拉框实际值，见[界面翻译](i18n.zh-CN.md)。可导入 JSON 位于 `example_workflows/`，测试会检查接线与默认运行时路径。实体文档的两个语言版本须同步修改，见[维护规范](guide.zh-CN.md)。

验证媒体依赖和真实节点时使用 ComfyUI 自己的 Python。纯测试可能跳过可选媒体/GPU 测试；有跳过的绿灯不代表 GPU 验证。硬件检查必须显式 opt-in 并有真实 NVIDIA 主机。集成脚本接受服务器、工作流和输入参数，先阅读各脚本的 `--help`。

使用独立测试数据根与 prefix。禁止清理共享 Wine prefix、按通用进程名称杀进程，或覆盖用户主工作流。日志可能包含绝对路径和素材名，公开分享前脱敏。

## 诊断 UI 传输

自定义 `dlss_*` UI 通道由 `diagnostic_ui()` 生成 JSON **字符串**列表。Comfy Jobs
会把任意 UI 列表内的字典当成媒体描述符；输入报告里的嵌套 `format` 对象不是 MIME
字符串。不要在这些列表里直接发送报告字典，也不要把诊断放入 `images`／`video`
通道。真实媒体描述符、内部报告对象和 STRING 插槽保持原有契约。前端共用解码器
同时接受旧对象，包括工作流属性中缓存的报告。

后端和前端须一起更新，保留未保存的工作流后重新加载前端。新执行使用安全传输；
此改动不重写或删除运行中服务器已持有的旧记录。仅有前端旧格式兼容，不能修复
服务器中旧记录的 Jobs 摘要。

缓存和流式导出都在 AAC 编码前，用 [FFmpeg atrim](https://ffmpeg.org/ffmpeg-filters.html#atrim)
将解码后的音频裁剪到可见视频终点，同时保留输出时长限制。这避免最后一个 AAC 帧
增加尾部时长，不改变视频帧数或平移音频时间戳。测试覆盖 48／44.1 kHz、短区间、
非整数帧率和非零起点；AAC 填充及有损编码不代表音频逐样本无损保留。

## 原生代码

当前 relay 只需要已有 Zig，不需要 NVIDIA NGX SDK：

```bash
bash sidecar/build_relay.sh
```

同时生成无需 GPU 的 mock Worker，仅用于传输测试，不是 NR 渲染器，也不进入 helper 包。原生光流分别为 Linux/Windows 构建，使用现有 Zig 与 CUDA API 头文件；命令及包布局见[分发说明](distribution.zh-CN.md)。

遵守 [C++ 质量规则](sidecar-cpp-quality.zh-CN.md)：有界输入、溢出检查、明确所有权、GPU 同步和安全清理。可移植 sanitizer 测试不能代替 Windows/Proton GPU 验证。

可选 [caller shim 构建及转发测试](caller-shim.zh-CN.md)需要外部官方 SDK 头文件。
它们不会加载 NR 或改动当前预设；不要把该 DLL 当成完整自有视频 Worker。

[自有 Worker 验证宿主](owned-worker.zh-CN.md)说明独立 Microsoft ABI 参数对象、有界
NR 探测与不使用 GPU 的目标平台 ABI 检查。[自有运行时](owned-runtime.zh-CN.md)说明
可选 Comfy 集成；旧安装方式保持不变。[SR/DLAA](super-resolution.zh-CN.md)提供同一个
Worker 的 SDK 构建路径、手动 Windows/MSVC 构建说明，以及源码/CPU 测试与 GPU 验收的区别。

保留的 `build_carrier.sh`、`build_renderer.sh` 是诊断路线，不是当前 NR 的前置条件。不要猜 ABI/vtable，也不要为链接研究版本而替换 MSVC 运行时符号。

## 发布

原创代码采用 MIT，NVIDIA API 头文件保留原声明。[第三方说明](../THIRD_PARTY_NOTICES.md)界定外部文件边界。Git 排除编译产物、helper、专有 Worker/模型、私有笔记、prefix、日志、缓存和测试素材。

发布前暂存目标源码，然后运行 `python3 scripts/check_publication.py`。它检查 **Git index** 的发布策略和疑似秘密，只打印位置/规则名；不是完整安全审计。仍需人工检查暂存内容、作者身份、第三方声明和发布目的地。

公共架构与用户行为放 `docs/`；本机测试记录、部署地址与交接笔记放被忽略的 `.agent-docs/`。
