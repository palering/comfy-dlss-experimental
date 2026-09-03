# DLSS NR Look

[English](nr-look.en.md) · 简体中文

Audience: public

节点暴露当前 D5V2 Worker 契约的所有 Look 字段，不代表 NVIDIA NR 的全部功能或 ReShade/RenoDX 的全部滤镜。使用卡片内 Comfy V3 标准输入，没有独立控制面板或新增前端依赖。

## 控制项

| 输入 | 默认 / 范围 | 用途与限制 |
| --- | --- | --- |
| nr_enabled | true | false 绕过 NR Worker |
| intensity | 0.25 / 0–3 | 模型强度；0 不保证完全不改图 |
| mix | 1 / 0–1 | 渲染后原图/结果混合；0 不启动 Worker，不是 NGX 参数 |
| nr_preset（高级） | 默认 / 实验 A、B、C | ID 0–3；不把未知语义包装成画质档位 |
| nr_style | 自然 / 默认、自然、电影感 | 对应 ID 0–2；社区名称不保证跨 DLL 相同外观 |
| local_tone_strength | 1 / 0–3 | 原生局部色调，不是独立调色滤镜 |
| local_structure_strength | 1 / 0–3 | 原生结构/细节，不是传统锐化 |
| skin_structure_strength | -1 / -1–3 | -1 保留运行时默认，无 Python 皮肤后处理 |
| automatic_mask | false | 请求运行时自动蒙版，不传外部蒙版 |
| ui_correction（高级） | false | 实验 UI 修正；无单独 UI 纹理，不保证保护字幕 |
| worker_profile（高级） | 当前兼容配置 / 实验 A、当前兼容、实验 B、C | 对应 ID 0–3，默认 1；不是 NR preset |

前三个 widget 与 schema-2 插槽不变；缺省新输入使用相同原生默认值。旧 schema-1 骨架设置明确拒绝。非整数、不有限/越界强度、非布尔标志在启动前拒绝，不静默截断。

卡片跟随 Comfy 语言，显示可读标签而非数字菜单；协议/输出仍保留 ID。前端加载时迁移旧整数 widget，后端也接受精确旧整数以兼容 API；未知标签、数字字符串或布尔值不强制转换。不覆盖用户工作流。未验证预设/配置留在高级项，不虚构 Quality 等语义。

Preview/Process 共用 profile_settings()/render_variant()。报告 settings 是实际传输设置，profile 是 UI 配置；这些能追溯结果，但不能证明闭源运行时内部采用每个字段。

## 使用顺序

1. 选短区间渲染默认 Look。
2. 每次改一个参数，再渲染；已准备光流可复用。滑动分割线只比较已有结果。
3. A 可连接第二套 Look，B 为编辑对象；A 不接则为适配原图。A 不是之前 B 的冻结快照。
4. 满意后接 Process 导出，用 100% 预览确认最终尺寸效果。

优先调 intensity、style、local_tone、local_structure。样例中 automatic_mask 也有响应，但空间语义未独立确认。皮肤、preset 和高级项仍属实验；数值更大不代表更好。mix 是非线性 RGBA8 混合，不是线性光色彩管理。

深度/外部蒙版/运动估计属于[输入引导](nr-input-reconstruction.zh-CN.md)，不是这些滑块实现。SR 尺寸、FG、seed、prompt、颗粒及任意 ReShade shader 不在协议内。预热/前置帧属于 History Settings，DLL/Proton 属于 Runtime。

<a id="measured-response"></a>
## 实测响应

2026-09-02：ComfyUI 0.34.0、Linux RTX 4070 Ti SUPER、GE-Proton 11-6，用户提供的 RTX40 video-converter 0.1.0 Worker/模型。素材 SDR 544×960、24 fps，前 0.5 秒以 50%（272×480、12 可见帧）渲染，120 次预热、DIS，其余默认。

哈希比较混合后、视频有损编码前的**可见 RGBA 字节**。旧默认、新显式默认和重复运行字节相同。哈希改变只证明像素响应，不证明改善、强度或预想语义。

| 改动 | 测试值 | 对比默认 |
| --- | --- | --- |
| nr_style | 0、2 | 不同 |
| intensity | 0、2 | 不同 |
| local_tone_strength | 0、2 | 不同 |
| local_structure_strength | 0、2 | 不同 |
| automatic_mask | true | 不同 |
| nr_preset | 1、2、3 | 相同 |
| skin_structure_strength | 0、2 | 相同 |
| ui_correction | true | 相同 |
| worker_profile | 0、2、3 | 相同 |
| mix | 0、0.5 | 不同；0 不启动 Worker |
| nr_enabled | false | 与 mix=0 相同；不启动 Worker |

相同结果不是普遍不支持的证明，可能受模型、素材、其他设置或缺少辅助纹理影响。每个头部字段独立检查过，不为无响应控件杜撰效果。

24 项参数/默认/绕过全部通过；非绕过任务残留所属进程为 0。双 Look 预览和非默认 Look 的 8 秒、544×960、192 帧输出成功，音频 8 秒，Process→Save Video 保留 BT.709/sRGB 标签。样例约 3.3–3.8 秒/半秒预览、6.3 秒/双 Look、23.9 秒/全尺寸导出，非性能承诺。

## 复现

check_nr_look.py 会实际排队 GPU 工作，仅在独立空闲测试实例运行。预先配置节点 1–6 的 API prompt、运行时和带标签输入：

```sh
python scripts/check_nr_look.py --url http://127.0.0.1:8199 \
  --prompt /path/to/preview-api.json --video tagged-test.mp4 \
  --report /path/to/nr-look-report.json
```

记录结果假定 24 fps、8 秒素材。脚本使用唯一工作流/client ID，检查实际原生值、重复哈希、绕过、双 Look、完整导出，拒绝起始忙队列；不要并行运行生产任务。无 DLL 下载或分发，无需重编译 C++。

check_nr_labels.py 比较命名选项/旧整数，检查默认像素相同及非法值拒绝。NR 分支被拒绝时 Comfy 仍可能排队独立输入检查分支，因此检查 node_errors，不只看 HTTP 状态。原验证有 87 Python / 6 JS 测试；当前覆盖以现有套件为准。
