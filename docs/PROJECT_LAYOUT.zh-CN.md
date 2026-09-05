# 项目与运行时目录结构

[English](PROJECT_LAYOUT.en.md) · 简体中文

Audience: public

Git checkout 只放源码、测试和模板；用户提供的运行库及生成数据放在 checkout 外，
避免 `git pull`、移除节点或切换版本时覆盖它们。

## 安装在 ComfyUI 下的仓库

```text
ComfyUI/
└── custom_nodes/
    └── comfy-dlss-experimental/
        ├── __init__.py                 # Comfy 入口，导出 WEB_DIRECTORY
        ├── comfy_dlss_experimental/
        │   ├── nodes/                  # Comfy V3 节点定义与执行入口
        │   ├── setup_check.py          # Setup Helper 的只读诊断逻辑
        │   ├── video_pipeline.py       # 输入准备、Worker 编排及导出
        │   ├── direct_nr.py            # D5V2 Worker 协议客户端
        │   ├── presets.py              # 预设校验与组件哈希
        │   ├── platform_runtime.py      # Windows 原生 / Linux Proton 选择
        │   └── ...                     # 输入、光流、预览与生命周期模块
        ├── web/                         # 节点内卡片与前端翻译
        ├── locales/{en,zh,zh-TW}/       # Comfy 节点标题、字段与提示
        ├── example_workflows/           # 可直接导入的当前工作流 JSON
        ├── examples/runtime-presets/    # 仅模板，不含专有 DLL
        ├── sidecar/
        │   ├── src/                     # 本项目 relay/NVOF helper 源码
        │   ├── vendor/nvof/             # NVIDIA 头文件来源及原声明
        │   ├── build/                    # Git 忽略的本地构建结果
        │   └── bin/<target>/<version>/  # 安装的本项目 helper Release（若有）
        ├── scripts/                      # 需显式运行的诊断/构建验证
        ├── tests/                        # 非 GPU 单测与集成测试框架
        ├── docs/                         # 中英文配对公开文档
        └── install.py                    # 只安装本项目有校验和的 helper ZIP
```

`sidecar/build/`、`sidecar/bin/`、外部 DLL、视频、缓存及私有 agent 记录均被忽略。
所以 clone 完成不等于已经拥有可执行 NR 运行时。

## 用户数据根

默认位置为 `ComfyUI/user/default/comfy-dlss-experimental/`。自定义 Comfy user
目录会改变前缀；`COMFY_DLSS_HOME` 可覆盖整个节点数据根。

```text
comfy-dlss-experimental/
├── components/
│   └── nr/
│       └── converter-v0.1.0-rtx40/     # 用户管理、按版本隔离的外部组合
│           ├── nvngx.dll               # 外部视频 Worker 可执行程序
│           └── nvngx_dlssnr.dll        # 与之匹配的 NR 模型/运行时
├── runtime-presets/
│   ├── default.json                    # 用户管理的当前绑定
│   └── another-version.json            # 可选的另一套已验证组合
├── prepared-clips/                     # 自动生成的颜色/运动缓存
├── runtime-snapshots/                  # 自动生成的哈希不可变副本
├── prefixes/                           # 自动生成的 Proton compatibility data
├── executions/                         # 自动生成、可复制的执行记录
├── cache/                              # 自动生成的 Worker/运行库缓存
├── tmp/                                # 自动生成的 Worker 临时文件
└── jobs/                               # 仅旧 carrier 诊断保留
```

只需人工准备 `components/` 与 `runtime-presets/`；其余目录在需要时自动创建。
不要修改 `runtime-snapshots/`；应修改源组件目录，并选择或创建新的预设。

## 进程边界

```text
Comfy 节点（Python）
  └─ dlss-native-relay.exe               # 本项目；PE 传输/进程管理器
       └─ nvngx.dll --video              # 外部 Worker；Windows PE 可执行程序
            └─ nvngx_dlssnr.dll          # 匹配的外部模型/运行时
```

Windows 原生运行两个 PE；Linux 使用已选 Proton 与 Xwayland。FFmpeg 与可选
NVIDIA 光流 helper 始终在宿主原生运行；整个节点不依赖 systemd 服务。

另见[外部运行时文件](DLL_PREPARATION.zh-CN.md)、
[安装与分发](distribution.zh-CN.md)及[架构](ARCHITECTURE.zh-CN.md)。
