import { t } from "./i18n.js";
// Pure report formatting: no Comfy globals and no HTML interpolation.
export function known(value, suffix = "") {
  return value === undefined || value === null || value === "" || value === "unknown" || value === "N/A"
    ? t("未知") : String(value) + suffix;
}
export function reportSections(report) {
  const v = report.source_video || {}, f = report.format || {}, a = report.active_view || {};
  const color = report.effective_color || {};
  const normalization = report.color_normalization;
  const transferName = value => ({bt709: "BT.709", "iec61966-2-1": "sRGB"})[value] || known(value);
  const dimensions = (w, h) => w && h ? `${w} × ${h}` : t("未知");
  const sections = [
    [t("文件与画面"), [
      [t("文件"), report.state === "deferred" ? t("内存或流式 VIDEO") : known(report.filename)], [t("容器识别器"), known(f.format_name)],
      [t("文件大小"), known(f.size, " bytes")], [t("编码 / 配置"), `${known(v.codec_name)} / ${known(v.profile)}`],
      [t("视频 / 总码率"), `${known(v.bit_rate, " bit/s")} / ${known(f.bit_rate, " bit/s")}`],
      [t("像素格式 / 位深"), `${known(v.pix_fmt)} / ${known(v.bits_per_raw_sample === "0" ? null : v.bits_per_raw_sample, " bit")}`],
      [t("编码尺寸"), dimensions(v.width, v.height)], [t("当前 VIDEO 尺寸"), dimensions(a.width, a.height)],
      [t("像素比例 / 显示比例"), `${known(v.sample_aspect_ratio)} / ${known(v.display_aspect_ratio)}`],
      [t("旋转"), known(v.side_data_list?.find(s => s.rotation !== undefined)?.rotation, t("°（标签）"))],
      [t("上游裁剪"), a.cropped === undefined ? t("未知") : a.cropped ? t("有，保留上游裁剪") : t("未检测到尺寸裁剪")],
    ]],
    [t("时间轴"), [
      [t("视频流 / 容器时长"), `${known(v.duration, " s")} / ${known(f.duration, " s")}`],
      [t("当前 VIDEO 时长"), known(a.duration, " s")],
      [t("上游截取起点 / 时长"), `${known(a.trim_start, " s")} / ${known(a.trim_duration, " s")}`],
      [t("平均 / 标称帧率"), `${known(v.avg_frame_rate)} / ${known(v.r_frame_rate)}`],
      [t("帧数"), known(v.nb_frames === "0" ? null : v.nb_frames) + t("（文件报告，未逐帧计数）")],
      [t("视频流起点"), known(v.start_time, " s")],
      [t("CFR / 解码验证"), t("待所选区间实际解码；文件头检查不保证整段可解码")],
    ]],
    [t("色彩：源标签 → 有效解释"), [
      ...[[t("矩阵"), "color_space"], [t("原色"), "color_primaries"], [t("传递曲线"), "color_transfer"], [t("范围"), "color_range"]]
        .map(([label, key]) => [label, `${known(v[key])} → ${known(color[key])}${report.assumptions?.some(x => x.field === key) ? t("（用户假设）") : ""}`]),
      [t("策略"), report.policy?.mode === "fill_missing" ? t("仅补全缺失标签，不覆盖已有标签") : t("严格读取标签")],
      [t("HDR 辅助信息"), (v.side_data_list || []).map(s => s.side_data_type).filter(Boolean).join(" · ") || t("未报告（不据此推断 SDR）")],
      [t("统一 sRGB"), report.policy?.normalize_to_srgb ? t("开启（SDR 实验）") : t("关闭（兼容原行为）")],
      [t("转换状态"), ({disabled: t("保留源传递曲线"), blocked: t("输入未通过检查，尚不能转换"),
        already_srgb: t("已经是 sRGB，不重复转换"), bt709_to_srgb: t("BT.709 → sRGB；实际转换像素")})[normalization?.operation] || t("待检查")],
      [t("工作空间"), normalization?.working_transfer ? t`BT.709 原色 · ${transferName(normalization.working_transfer)} · RGB 全范围 · RGBA8` : t("待输入检查")],
      [t("导出传递曲线"), normalization?.output_transfer ? t`${transferName(normalization.output_transfer)}（A/B 一致）` : t("待输入检查")],
    ]],
    [t("音频"), report.audio?.length ? report.audio.flatMap((s, index) => [
      [t`音轨 ${index + 1}`, `${known(s.codec_name)} · ${known(s.sample_rate, " Hz")} · ${known(s.channels, t(" 声道"))}`],
      [t("时长 / 起点"), `${known(s.duration, " s")} / ${known(s.start_time, " s")}`],
    ]) : [[t("音轨"), report.audio ? t("无") : t("未知")]]],
    [t("准备计划"), [
      [t("运动模式"), ({zero: t("零矢量；保留镜头检测与 NR 历史"), dis: t("DIS 光流估计"), nvidia: t("NVIDIA 硬件光流（原生 Linux）")})[report.guide_mode] || t("未知")],
      [t("引导状态"), t("此节点仅预检；计算与缓存命中见预览/处理报告")],
      [t("运行环境"), t("本检查不启动 Worker，也不验证 GPU / Proton")],
      ...(report.plan || []).map((line, i) => [String(i + 1), t(line)]),
    ]],
  ];
  if (report.media_tools) {
    const tools = report.media_tools;
    sections.push([t("媒体工具（宿主系统）"), [
      [t("调用方式"), t("外部 FFmpeg / ffprobe；原生运行，不经过 Proton")],
      ...["ffmpeg", "ffprobe"].flatMap(name => {
        const tool = tools[name] || {};
        return [[name, tool.available ? t("可用") : t("不可用")],
          [t("解析路径"), known(tool.path)],
          [t("来源"), ({PATH: t("Comfy 后端 PATH"), explicit: t("手动配置"), ffmpeg_directory: t("FFmpeg 所在目录")})[tool.source] || t("未知")],
          [t("版本"), known(tool.version)],
          ...(tool.error ? [[t("错误"), tool.error]] : [])];
      }),
      ["PyAV", known(tools.pyav_version)],
      [t("说明"), t("PyAV 是 Python 解码库，不代表已安装 ffmpeg / ffprobe 命令行工具。以上为本次检查快照。")],
    ]]);
  }
  return sections;
}

export function reportIssue(issue, report) {
  if (issue.field && issue.kind === "needs_confirmation") {
    return t`缺少 ${issue.field}。请在视频输入适配中选择 fill_missing，并指定传递曲线和范围。`;
  }
  if (issue.field && issue.kind === "unsupported") {
    return t`当前 NR 输入不支持 ${issue.field}=${known(report.source_video?.[issue.field])}；补全模式不会覆盖已知标签，也不会执行 HDR 色调映射。`;
  }
  // Unknown errors are preserved literally, never guessed or evaluated.
  return t(issue.message);
}
