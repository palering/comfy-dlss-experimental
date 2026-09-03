// Copy the recorded run, never the current (possibly unsubmitted) widget values.
export function formatPreviewDiagnostics(session) {
  if (!session) throw new Error("尚无预览任务详情");
  const fields = [
    "session_id", "node_id", "workflow_id", "created_at", "revision", "state", "error",
    "preview_mode", "start_time", "duration", "cursor_time", "selected_start", "preview_scale",
    "process_to_end", "input_duration", "range_duration",
    "source", "frame_rate", "frame_count", "runtime", "contract", "profile_a", "profile_b",
    "guide_mode", "guide_settings", "guide_cache_hit", "input_report", "report", "progress",
  ];
  const preview = Object.fromEntries(fields.filter(key => Object.hasOwn(session, key))
    .map(key => [key, session[key]]));
  // Full results can contain redundant media descriptors. The execution's parameters,
  // source, timings, component hashes and resource samples are the diagnostic record.
  const execution = session.execution
    ? Object.fromEntries(Object.entries(session.execution).filter(([key]) => key !== "result"))
    : null;
  return JSON.stringify({
    format: "comfy-dlss-experimental.preview-diagnostics",
    schema_version: 1,
    notes: [
      "本报告对应记录中的任务，不代表之后尚未执行的节点修改。",
      "耗时为节点内主机墙钟时间，非纯 GPU 推理时间，不含排队及上游节点。",
      "color_conversion、optical_flow、cache_write 是 input_preparation 的子项，不能重复相加。",
      "常驻模式 worker_startup 是 worker_acquire 的子项，不能重复相加。",
      "资源是离散采样，可能漏掉峰值；GPU 利用率为整卡数据。",
      "包含本地文件路径与环境信息，公开分享前请检查。",
    ],
    preview,
    execution,
  }, null, 2);
}

// Plain HTTP LAN pages may lack the async Clipboard API. Keep a visible, selected
// report when the legacy fallback is also blocked; never report a false success.
export async function copyDiagnosticText(text, { clipboard, document, textbox }) {
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return "copied";
    } catch { /* Permission/context restrictions: try the selected-text fallback. */ }
  }
  if (!textbox.isConnected) return "unavailable";
  const previousFocus = document.activeElement;
  textbox.value = text;
  textbox.hidden = false;
  textbox.focus({ preventScroll: true });
  textbox.select();
  textbox.setSelectionRange(0, text.length);
  let copied = false;
  try { copied = document.execCommand?.("copy") === true; }
  catch { /* Leave the report selected for manual copy. */ }
  if (!copied) return "manual";
  textbox.hidden = true;
  textbox.value = "";
  if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
  return "copied";
}
