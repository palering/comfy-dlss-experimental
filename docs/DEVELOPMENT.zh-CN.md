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

## 原生代码

当前 relay 只需要已有 Zig，不需要 NVIDIA NGX SDK：

```bash
bash sidecar/build_relay.sh
```

同时生成无需 GPU 的 mock Worker，仅用于传输测试，不是 NR 渲染器，也不进入 helper 包。原生光流分别为 Linux/Windows 构建，使用现有 Zig 与 CUDA API 头文件；命令及包布局见[分发说明](distribution.zh-CN.md)。

遵守 [C++ 质量规则](sidecar-cpp-quality.zh-CN.md)：有界输入、溢出检查、明确所有权、GPU 同步和安全清理。可移植 sanitizer 测试不能代替 Windows/Proton GPU 验证。

保留的 `build_carrier.sh`、`build_renderer.sh` 是诊断路线，不是当前 NR 的前置条件。不要猜 ABI/vtable，也不要为链接研究版本而替换 MSVC 运行时符号。

## 发布

原创代码采用 MIT，NVIDIA API 头文件保留原声明。[第三方说明](../THIRD_PARTY_NOTICES.md)界定外部文件边界。Git 排除编译产物、helper、专有 Worker/模型、私有笔记、prefix、日志、缓存和测试素材。

发布前暂存目标源码，然后运行 `python3 scripts/check_publication.py`。它检查 **Git index** 的发布策略和疑似秘密，只打印位置/规则名；不是完整安全审计。仍需人工检查暂存内容、作者身份、第三方声明和发布目的地。

公共架构与用户行为放 `docs/`；本机测试记录、部署地址与交接笔记放被忽略的 `.agent-docs/`。
