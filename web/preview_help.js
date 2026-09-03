import { t } from "./i18n.js";
// Static, plain-text help: do not read unsent widget values as rendered results.
export const previewOutputNotice = () => t("保存 video_b/video_a 保存的是本次预览素材，不会自动输出完整原尺寸视频。");
export const previewParameterHelp = () => [
  [t("预览起点 · start_time"), t("相对输入 VIDEO 的起点，单位为秒；上游已裁剪时从裁剪后的视频计时。")],
  [t("处理到末尾 / 手动区间长度"), t("勾选“处理到视频末尾”后自动读取剩余时间，忽略手动 duration；起点 0 = 完整视频。不勾选时，duration 才决定片段长度，例如起点 10、长度 3 生成第 10–13 秒。duration 不是每批处理时长，也不是预热时长；固定 30 秒限制已取消。")],
  [t("游标偏移 · cursor_time"), t("单帧位置 = 起点 + 区间内偏移。起点 10、偏移 1.5，取第 11.5 秒处按帧时间对齐的一帧。偏移必须小于区间长度；片段模式忽略它。")],
  [t("预览尺寸 · preview_scale"), t("50/75/100 是宽、高各自的百分比。50% 像素数约为 1/4，会实际缩小 NR 输入与 VIDEO 输出，不是播放器缩放、效果强度或超分倍率。最终尺寸效果用 100% 检查。")],
  [t("排队模式 · preview_mode"), t("顶部运行按钮使用此项：range = 片段，frame = 单帧。卡片的“渲染当前帧 / 渲染片段”分别指定本次模式，不更改这里保存的值，也不执行下游保存视频。")],
  [t("当前效果 B / 对照效果 A"), t("B 接正在调节的 NR Look；A 不接时为同尺寸、同色彩处理的输入对照。A 接另一套 Look 可比较两种效果，通常增加处理耗时，不会自动冻结该 Look 参数。")],
  [t("内部机制在哪里设置"), t("输出范围不控制处理批次。历史预读/预热在 DLSS History Settings；Worker 常驻、释放和运行库在 Runtime Configuration；光流在 Video Guides 及其提供器节点。当前连续逐帧处理，没有按 2 秒或 30 秒切批；长视频缓存落盘，不整段装进显存。")],
  [t("历史设置 · contract（可选）"), t("接 DLSS History Settings 控制前置历史与初始化预热；默认前置 0.5 秒、新实例预热 120 次。它们不追加到输出时长。兼容常驻实例复用初始化，但每次任务仍重置历史并处理前置帧。")],
  [t("输出与正式保存"), t("video_b 是 B 的预览结果；video_a 是 A 的预览对照；session/status 是会话与诊断信息，不是视频。单帧模式输出只有一帧，片段模式保留选定范围和尺寸。正式输出可另接 DLSS Process Video → 保存视频，共用输入、Look、Runtime，独立设置输出范围；Process 同样提供到末尾开关，旧 duration=0 仍兼容剩余全部。两种方式都会先检查帧数和可用磁盘，不会静默截成 30 秒。")],
  [t("对比按钮与旧画面"), t("滑动、并排、交替、差异只改变浏览器显示，不会改变 video_b/video_a，也不会导出分屏画面。修改参数后需重新渲染；当前显示的旧结果不会自动更新。单帧只适合初看效果，运动和闪烁请用片段检查。")],
];
