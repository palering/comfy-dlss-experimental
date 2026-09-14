# 实验性 SR / DLAA

[English](super-resolution.en.md) · 简体中文

Audience: public

独立[显式相机 Streamline CXR1 后端](streamline-reconstruction.zh-CN.md)已通过
SR、DLAA、RR、RR+DLAA 的有界 Python→Worker GPU 序列验证。它尚未接入这些节点：
显式相机／G-buffer 契约与生命周期不同，不会静默替换本页 CSR1／`owned_sr` 集成，
也不代表后者已经验收。
已验证 CXR1 路线请使用独立的 [owned_sl 重建节点](reconstruction-nodes.zh-CN.md)，
它们不接受仅有普通 VIDEO 的输入。
已有 VIDEO 并具备显式相机／深度时，可使用新增的
[统一处理链 Streamline 阶段](streamline-video-input.zh-CN.md)。

**状态：ComfyUI → Python → CSR1 Worker → 官方 NGX SDK 的源码链路已接入；
SDK 链接版 Worker 与 GPU 输出仍需验收。**现有 NR 构建不会启用 SR。
源码／CPU 测试、输入计划有效或文件检查通过，都不代表 GPU 验证完成。

## 节点与连接

```text
加载视频 → Video Input Adapter → Input Assembler → SR / DLAA Stage → Pipeline Render → 保存视频
             归一化到 sRGB           ↑                 ↑                  ↑
                              光流提供者 + 深度     SR Settings      owned_sr Runtime
```

在没有效果阶段的输入处理链上添加一个 SR/DLAA 阶段。SR Settings 选择功能模式，
SR Stage 指定输出尺寸，Pipeline Render 只控制输出时间范围，其百分比尺寸必须
保持 **100**。DLAA 保持素材原尺寸，SR 使用明确指定的更大目标；输出由 Worker
生成，而不是缩放滤镜。结果 VIDEO 连接原生 Save Video。

[实验工作流](../example_workflows/dlss_sr_experimental.json)是连接模板，**不是导入即可
运行的例子**：先准备匹配的数值深度清单和启用 SDK 的 `owned_sr` 预设。

**SR Input Plan 仍是不渲染的检查器。**它接收 SR Settings，以及 sequence 或组装后的
pipeline 二选一；不解码帧、不估计光流、不初始化 NGX、不读取全部引导张量，也不启动
Worker。其 `runnable: false` 表示报告本身不是执行或运行时验收，不是输出 VIDEO 的节点；
实际执行需要 SR Stage → Pipeline Render。

| 控件 | 含义 |
| --- | --- |
| 模式 | DLAA 保持实际素材尺寸；Quality／Balanced／Performance／Ultra Performance 请求 SR 质量模式，不等同固定放大倍数，也不是 NR 风格。 |
| 阶段输出宽／高 | 显式指定比素材更大的 SR 目标，精确保持宽高比。DLAA 忽略这两个值，保留素材原尺寸。 |
| 模型预设 | 模型默认或 J/K/L/M 等 SDK 提示，兼容性和行为取决于运行库，建议先用默认。 |
| 反向 Z 深度 | 声明真实设备深度编码，不是对深度图取反或转换的开关。 |
| NGX 自动曝光 | 当前媒体执行器保持开启。底层契约支持手动曝光元数据，但这条媒体路径不提供。 |
| 素材抖动策略 | 使用成片零抖动。原始渲染元数据仍只是契约选项，不是已实现的媒体输入。 |
| 运动含渲染抖动 | 当前媒体执行器保持关闭；不要为成片虚构采样偏移。 |
| 声明 HDR 输入 | 当前执行器不支持，启用后在 GPU 执行前明确报错。 |

共享校验现使用最长边与总像素预算，详见 [Streamline 尺寸说明](streamline-video-input.zh-CN.md)。
旧 CSR1 Worker 仍受原横屏握手边界约束；本次原画幅竖屏验收仅针对 CXR1，不代表
独立 CSR1 也已通过。软件预算不代表 GPU 能力；SDK 查询仍须接受质量模式与尺寸。

## 必需的重建输入

| 输入 | 传输格式 | 必须表达的含义 |
| --- | --- | --- |
| 颜色 | 小端 RGBA16F | 当前媒体路径仅接受明确归一化到 sRGB 工作传递函数的 SDR 像素；FP16 本身不等于线性或 HDR。 |
| 运动 | 小端 RG16F | 当前到前帧、输入像素单位、左上角 XY，可由 DIS、NVIDIA 光流或匹配外部光流提供估计。 |
| 深度 | 小端 R32F | 有限设备深度 [0, 1]，真实正向／反向 Z 编码，以及渲染深度或校准投影的来源。 |
| 逐帧元数据 | 数值元数据 | 对应同一帧的 PTS、帧间隔和历史重置标记；当前媒体路径使用零抖动及自动曝光。 |

深度是**必需项**。把 External Numerical Guide 接到组装节点的 depth 输入，使用
`device_z`，且素材哈希、视图、输入网格、时间戳均匹配，**包含前置历史帧**。
PNG 可视化、白膜渲染图或单目相对深度都不等于设备深度，修改标签并不完成必要的校准。
参见[外部引导格式](external-guides.zh-CN.md)。

运动可以使用所选 DIS/NVIDIA 提供者或校验后的外部引导。零运动仅用于诊断，不能代替
运动视频所需的有效运动场。所有平面必须对应同一帧；首帧、切镜和序列不连续时重置
历史。法线、材质和光追缓冲不属于此处必需输入，这**不是 Ray Reconstruction（光线重建）**。

成片缺少独立亚像素抖动渲染样本，光流不能重建那些样本，也不能保证遮挡显露／透明
区域运动正确。因此输入有效不等于达到了游戏原生输入的 SR 画质。

## 运行时文件与构建

SR 仍使用**同一个本项目 Worker 程序**，但要求启用官方 SDK 的构建，使用独立
`owned_sr` 预设及 CSR1 协议，不使用 NR 的薄 caller。

```text
节点用户数据目录/
  runtime-presets/owned-sr.json
  components/owned-sr/
    comfy-dlss-worker.exe        我们的启用 SDK 的 Windows 程序
    nvngx_dlss.dll               用户提供的 SR 模型／运行库
```

把 [SR 预设](../examples/runtime-presets/owned-sr.example.json)复制到上面的预设位置，
填写明确路径。`compatibility.project_id` 必须为规范的非零 UUID，用于标识集成项目，
不是显卡支持认证。运行时选择检查文件哈希，之后 CSR1 握手确认所选 Worker 真的包含
SR；默认 Zig NR 构建会报告未编译 SR，而不是静默回退成 NR。

已有 Windows x64 MSVC 工具链与官方 SDK 的开发者可以执行：

```powershell
cmake -S sidecar -B sidecar/build-sdk -A x64 -DCOMFY_NGX_SDK_ROOT=C:/path/to/NVIDIA-DLSS
cmake --build sidecar/build-sdk --config Release
ctest --test-dir sidecar/build-sdk -C Release --output-on-failure
```

Worker 目标为 `comfy-dlss-worker`，输出
`sidecar/build-sdk/Release/comfy-dlss-worker.exe`。`caller` 目标输出
`sidecar/build-sdk/caller/Release/nvngx.dll`，只供 NR 使用。默认 SDK 库为
`lib/Windows_x86_64/x64/nvsdk_ngx_d.lib`，需要时可覆盖 `COMFY_NGX_SDK_LIBRARY`。
`_d` 表示动态 CRT，此构建使用 `/MD`，不是调试 CRT。

SDK 库需要兼容的 MSVC 运行时／启动库。Zig 能单独编译 C++ 源码，不代表已经成功
链接 SDK Worker；现有精简 Zig NR 工具链不提供这些库。仓库提供手动触发的
[SDK Worker 构建工作流](../.github/workflows/build-sdk-worker.yml)，供 Windows 构建
环境使用；文件存在不等于已有公开二进制，也不代表构建或 GPU 运行已经完成。
它不打包 NVIDIA DLL、SDK 库或模型。
该开发 ZIP 不是 `install.py` 辅助程序包：核对校验和及构建来源后，把明确选定的 Worker
放到预设路径。`/MD` 构建还需要执行环境提供兼容的 Microsoft Visual C++ x64
运行库，Windows 或 Proton prefix 须另行满足此依赖。

SDK 声明参考 [NVIDIA 官方 DLSS 仓库](https://github.com/NVIDIA/DLSS)。项目不分发专有
运行库，不要用 `nvngx_dlss.dll` 替换 NR 模型槽；NR、SR 绑定应分别维护。

## 执行与验收边界

- Windows 直接执行 PE，Linux 使用选定的 Proton。即使上游 SDK 另有 Linux 组件，
  本项目这条路径也不是原生 Linux SR 执行器。
- SR 当前要求**任务后释放／isolated**，未实现 SR 常驻复用或节点内 SR A/B 预览。
- 逐帧流式处理限制解码数据和中间内存，不生成整片原始颜色／运动／深度缓存。
  “保留准备缓存”开关适用于 NR，不适用于 SR；编码输出和任务记录仍占磁盘。
- 前置历史可送入更早帧但不导出它们；NR 的重复预热不应用于 SR。输出起点／时长仍单独控制。
- HDR、手动曝光、外部渲染抖动、混合 NR/SR 栈、多次 SR、FG 和光线重建明确拒绝或尚不可用，不做模拟替代。
- 源码、契约和模拟传输测试不等于 Windows 或 Linux/Proton SR GPU 验收，也不证明画质或长视频稳定性。

已有[自有 NR 工作流](owned-runtime.zh-CN.md)保持独立，不需要下载 SR 文件或构建 SDK 版本。
