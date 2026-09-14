# 光流与 SR／DLAA 对比

简体中文 · [English](flow-comparison.en.md)

Audience: public

## 各个控制项相互独立

DIS 是传统 CPU 光流估计，不是深度学习模型，也不是填充零运动。当前 Balanced 使用
OpenCV MEDIUM、最细金字塔层 1；Quality 使用 MEDIUM、最细层 0。Video Guides 还独立
默认采用 50% 宽高分析。因此，只切换光流提供器不等于按原图精度分析。明确改为 100%
才能对比原尺寸分析；这改变光流开销，不改变 RGB、SR 输出尺寸，也不重新计算几何。

NVIDIA Flow 通过宿主原生辅助程序调用 GPU 光流加速器，不是神经网络光流，也不是
Proton 中的 DLSS Worker。先查询设备支持，再选择 1／2／4 像素输出网格；更密并不保证
估计更准确。前后向有独立历史，切镜重置，不会静默退回 DIS。缺失辅助程序时，应使用
已有构建依赖或经批准的包，在项目内准备，不能因此全局安装。

Flow Selector 仅请求选中的懒加载提供器。将其连到 Video Guides，再把 settings
连到 Video Input Adapter；Input Assembler 继承相同设置。不要又在 Assembler 上
连接一个意外覆盖前述选择的提供器。分析百分比与提供器档位是两个独立控制量。

## 受控对比

保持源像素、已保存几何、区间、前置历史、重置规则、SR 模式／模型预设和编码设置
相同，每次只改变一个因素：

1. DIS Balanced：50% 对 100% 分析。
2. 固定 100%：DIS Balanced 对 DIS Quality。
3. 固定 100%：DIS Quality 对 NVIDIA Quality，记录网格和历史提示开关。
4. 每个 2× SR 与同输出尺寸的 2× Lanczos 基线比较。
5. 原尺寸 DLAA 单独与原尺寸输入比较；把 DLAA 显示放大只是对比展示，不是 DLAA 超分。

当前 StreamEncoder 使用 H.264、x264 fast、CRF 16。同 CRF／同参数**不等于同码率或
同文件大小**；评估成品压缩效率还需要等码率／等体积对照，不能从文件大小推导画质。
检查 1:1 局部和动态边缘、发丝、细线、遮挡、拖影、闪烁。光流重投影误差和前后向
一致性只是诊断，不是真值画质评分，模糊或遮挡会影响它们。逐组保存配置、资源、输出
哈希及完整文件，单组失败不影响其他已完成结果。

## 合理的处理路径

默认分成两条独立路径：原视频＋引导 → SR → 编码；或原视频＋引导 → DLAA → 编码。
SR 本身已经包含时序重建／抗锯齿。当前单阶段 VIDEO 执行器不支持 SR → DLAA。
如果后续专门实验双遍，需要高分辨率运动矢量（正确重采样并换算像素单位，或重新估计）、
匹配的深度／标定／源身份、第二遍独立历史，以及未压缩中间结果。直接复用低分辨率
引导，或编码重载后不重新绑定，都是无效组合。双重时序过滤可能变软或放大伪影，不能
预设其一定改善画质。

学习型光流（如 RAFT 类模型）可作为后续可选提供器，目前未安装。需要审查权重与许可、
隔离运行依赖、测量内存，并验证方向、单位、遮挡和历史重置。成品视频仍缺少独立抖动
渲染采样和引擎真值几何，换光流模型不能保证恢复这些缺失信息。

参见[尺寸准入](streamline-video-input.zh-CN.md)、
[OpenCV DIS](https://docs.opencv.org/4.x/de/d4f/classcv_1_1DISOpticalFlow.html)、
[NVIDIA 光流](https://docs.nvidia.com/video-technologies/optical-flow-sdk/nvofa-programming-guide/index.html)
和 [Streamline DLSS 集成](https://github.com/NVIDIA-RTX/Streamline/blob/v2.14.1/docs/ProgrammingGuideDLSS.md)。
