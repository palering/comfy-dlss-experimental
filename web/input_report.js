// Pure report formatting: no Comfy globals and no HTML interpolation.
export function known(value, suffix = "") {
  return value === undefined || value === null || value === "" || value === "unknown" || value === "N/A"
    ? "未知" : String(value) + suffix;
}
export function reportSections(report) {
  const v = report.source_video || {}, f = report.format || {}, a = report.active_view || {};
  const color = report.effective_color || {};
  const normalization = report.color_normalization;
  const transferName = value => ({bt709: "BT.709", "iec61966-2-1": "sRGB"})[value] || known(value);
  const dimensions = (w, h) => w && h ? `${w} × ${h}` : "未知";
  const sections = [
    ["文件与画面", [
      ["文件", known(report.filename)], ["容器识别器", known(f.format_name)],
      ["文件大小", known(f.size, " bytes")], ["编码 / 配置", `${known(v.codec_name)} / ${known(v.profile)}`],
      ["视频 / 总码率", `${known(v.bit_rate, " bit/s")} / ${known(f.bit_rate, " bit/s")}`],
      ["像素格式 / 位深", `${known(v.pix_fmt)} / ${known(v.bits_per_raw_sample === "0" ? null : v.bits_per_raw_sample, " bit")}`],
      ["编码尺寸", dimensions(v.width, v.height)], ["当前 VIDEO 尺寸", dimensions(a.width, a.height)],
      ["像素比例 / 显示比例", `${known(v.sample_aspect_ratio)} / ${known(v.display_aspect_ratio)}`],
      ["旋转", known(v.side_data_list?.find(s => s.rotation !== undefined)?.rotation, "°（标签）")],
      ["上游裁剪", a.cropped === undefined ? "未知" : a.cropped ? "有，保留上游裁剪" : "未检测到尺寸裁剪"],
    ]],
    ["时间轴", [
      ["视频流 / 容器时长", `${known(v.duration, " s")} / ${known(f.duration, " s")}`],
      ["当前 VIDEO 时长", known(a.duration, " s")],
      ["上游截取起点 / 时长", `${known(a.trim_start, " s")} / ${known(a.trim_duration, " s")}`],
      ["平均 / 标称帧率", `${known(v.avg_frame_rate)} / ${known(v.r_frame_rate)}`],
      ["帧数", known(v.nb_frames === "0" ? null : v.nb_frames) + "（文件报告，未逐帧计数）"],
      ["视频流起点", known(v.start_time, " s")],
      ["CFR / 解码验证", "待所选区间实际解码；文件头检查不保证整段可解码"],
    ]],
    ["色彩：源标签 → 有效解释", [
      ...[["矩阵", "color_space"], ["原色", "color_primaries"], ["传递曲线", "color_transfer"], ["范围", "color_range"]]
        .map(([label, key]) => [label, `${known(v[key])} → ${known(color[key])}${report.assumptions?.some(x => x.field === key) ? "（用户假设）" : ""}`]),
      ["策略", report.policy?.mode === "fill_missing" ? "仅补全缺失标签，不覆盖已有标签" : "严格读取标签"],
      ["HDR 辅助信息", (v.side_data_list || []).map(s => s.side_data_type).filter(Boolean).join(" · ") || "未报告（不据此推断 SDR）"],
      ["统一 sRGB", report.policy?.normalize_to_srgb ? "开启（SDR 实验）" : "关闭（兼容原行为）"],
      ["转换状态", ({disabled: "保留源传递曲线", blocked: "输入未通过检查，尚不能转换",
        already_srgb: "已经是 sRGB，不重复转换", bt709_to_srgb: "BT.709 → sRGB；实际转换像素"})[normalization?.operation] || "待检查"],
      ["工作空间", normalization?.working_transfer ? `BT.709 原色 · ${transferName(normalization.working_transfer)} · RGB 全范围 · RGBA8` : "待输入检查"],
      ["导出传递曲线", normalization?.output_transfer ? `${transferName(normalization.output_transfer)}（A/B 一致）` : "待输入检查"],
    ]],
    ["音频", report.audio?.length ? report.audio.flatMap((s, index) => [
      [`音轨 ${index + 1}`, `${known(s.codec_name)} · ${known(s.sample_rate, " Hz")} · ${known(s.channels, " 声道")}`],
      ["时长 / 起点", `${known(s.duration, " s")} / ${known(s.start_time, " s")}`],
    ]) : [["音轨", report.audio ? "无" : "未知"]]],
    ["准备计划", [
      ["运动模式", ({zero: "零矢量；保留镜头检测与 NR 历史", dis: "DIS 光流估计", nvidia: "NVIDIA 硬件光流（原生 Linux）"})[report.guide_mode] || "未知"],
      ["引导状态", "此节点仅预检；计算与缓存命中见预览/处理报告"],
      ["运行环境", "本检查不启动 Worker，也不验证 GPU / Proton"],
      ...(report.plan || []).map((line, i) => [String(i + 1), line]),
    ]],
  ];
  return sections;
}
