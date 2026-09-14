import { getLocale } from "./i18n.js";

export const PIPELINE_CARD_NODES = new Set(["DLSSExperimentalInputAssembler", "DLSSExperimentalNRStage", "DLSSExperimentalSRPlan", "DLSSExperimentalSRStage", "DLSSExperimentalStreamlineStage"]);

export function pipelineLabels() {
  return getLocale() === "zh" ? {
    title: "输入与处理计划", refresh: "检查计划（不渲染）", copy: "复制详情",
    empty: "尚未检查", stale: "配置或连接已修改，请重新检查",
    note: "配置检查不是 GPU 验收；只在预览／输出时处理请求区间。",
    copied: "已复制", manual: "请手动复制下方选中的内容", details: "完整详情",
    queued: "已提交检查", failed: "检查提交失败", configured: "配置已组装",
    blocked: "输入未通过", source: "素材尺寸", duration: "素材时长（秒）",
    provider: "运动提供者", usage: "运动用途", count: "处理阶段数",
    required: "功能执行时计算", deferred: "等待右侧功能需求",
    not_required_bypass: "全部直通，不计算光流", unknown: "未知",
    history: "时序与文件内容将在请求区间验证；未探测运行环境。",
    attachments: "扩展信息", unused: "当前后端不消费", output: "目标尺寸", missing: "缺少输入",
    feature: "功能", srState: "SR 需要 SDK Worker 与 GPU 验证", slState: "Streamline 需要 owned_sl；本计划未运行 GPU", externalRequired: "读取外部数值数据",
  } : {
    title: "Input and processing plan", refresh: "Check plan (no render)", copy: "Copy details",
    empty: "Not checked", stale: "Settings or links changed; check again",
    note: "Configuration check is not GPU acceptance. Preview/Render processes only the requested range.",
    copied: "Copied", manual: "Copy the selected text below manually", details: "Full details",
    queued: "Check submitted", failed: "Check submission failed", configured: "Configuration assembled",
    blocked: "Input blocked", source: "Media dimensions", duration: "Media duration (seconds)",
    provider: "Motion provider", usage: "Motion usage", count: "Feature stages",
    required: "Compute when feature executes", deferred: "Awaiting feature requirements",
    not_required_bypass: "All bypass; no optical flow", unknown: "Unknown",
    history: "Timing and content are checked for the requested range; runtime not probed.",
    attachments: "Extra guides", unused: "Not consumed by this backend", output: "Target dimensions", missing: "Missing inputs",
    feature: "Feature", srState: "SR requires SDK Worker and GPU validation", slState: "Streamline requires owned_sl; this plan has not run the GPU", externalRequired: "Read external numeric data",
  };
}

export function pipelineRows(report) {
  const l = pipelineLabels(), source = report?.source || {};
  const known = v => v === null || v === undefined ? l.unknown : String(v);
  if (report?.kind === "sr_input_plan") return [
    [l.feature, String(report.feature || "SR").toUpperCase()],
    [l.source, `${known(report.input?.width)} × ${known(report.input?.height)}`],
    [l.output, `${known(report.output?.width)} × ${known(report.output?.height)}`],
    [l.missing, (report.missing_inputs || []).join(", ") || "—"],
    [l.usage, l.srState],
  ];
  return [
    [l.source, `${known(source.width)} × ${known(source.height)}`],
    [l.duration, known(source.duration)],
    [l.provider, known(report?.motion?.provider)],
    [l.usage, l[report?.motion?.usage] || l.unknown],
    [l.count, known(report?.stages?.length)],
    ...(report?.feature === "sr" || report?.feature === "dlaa" ? [
      [l.feature, report.feature.toUpperCase()],
      [l.output, `${known(report.output?.width)} × ${known(report.output?.height)}`],
      [l.missing, (report.missing_inputs || []).join(", ") || "—"],
      [l.usage, report?.stages?.[0]?.backend === "owned_cxr1_sl" ? l.slState : l.srState],
    ] : []),
    ...(report?.attachments || []).map(item => [known(item.role),
      item.usage === "required" ? l.externalRequired : item.usage === "deferred" ? l.deferred : l.unused]),
  ];
}
