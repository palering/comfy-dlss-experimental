import { t } from "./i18n.js";
import { previewPrompt } from "./preview_state.js";
import { decodeDiagnosticReport } from "./diagnostic_report.js";

export function inspectionMessage(state) {
  const labels = {
    submitting: "正在提交检查", queued: "检查排队中", running: "正在执行检查及上游",
    no_report: "任务已结束，本节点未返回检查报告；请检查上游重建开关、采样兼容性或跳过状态。",
    failed: "检查执行失败", interrupted: "检查已中断", submit_failed: "提交失败",
    stale: "配置或上游已变化，本次结果已过期，请重新检查",
    unknown: "无法确认检查结果，请查看任务历史；不会自动重试。",
  };
  return `${t(labels[state.kind] || "尚未检查")}${state.detail ? `：${state.detail}` : ""}`;
}

export function inspectionError(error) {
  const response = error?.response;
  if (!response) return String(error?.message || error);
  const lines = [response.error?.message, response.error?.details];
  for (const [id, node] of Object.entries(response.node_errors || {}))
    for (const issue of node.errors || []) lines.push(`${id}: ${issue.message || ""} ${issue.details || ""}`.trim());
  return lines.filter(Boolean).join("\n") || String(error.message || error);
}

// One explicitly submitted inspection at a time. Events may arrive before the
// POST response; retain only report/terminal events until its prompt_id is known.
// No global queue hooks, model switches, graph edits or automatic resubmission.
export function createInspectionTask({api, app, node, reportKeys, onReport, onState,
  pollMs = 3000, schedule = setTimeout, unschedule = clearTimeout}) {
  let active = null, disposed = false, lastState = null;
  const snapshot = async () => {
    const graph = await app.graphToPrompt();
    const output = previewPrompt(graph.output, node.id);
    return {workflow: graph.workflow, output};
  };
  const fingerprint = prompt => JSON.stringify(Object.entries(prompt.output)
    .sort(([a], [b]) => a.localeCompare(b)).map(([id, n]) => [id, n.class_type, n.inputs]));
  const reportFrom = output => {
    for (const key of reportKeys) {
      const report = decodeDiagnosticReport(output?.[key]?.[0]);
      if (report) return report;
    }
    return null;
  };
  function emit(kind, detail = "") {
    lastState = {kind, detail, busy: !!active};
    if (!disposed) onState(lastState);
  }
  function cleanup(run) {
    unschedule(run.timer);
    for (const [type, listener] of run.listeners) api.removeEventListener(type, listener);
  }
  function finish(run, kind, detail = "", report = null) {
    if (active !== run || disposed) return;
    cleanup(run); active = null;
    if (report) { lastState = null; onState({kind: "reported", busy: false}); onReport(report); }
    else emit(kind, detail);
  }
  async function read(path) {
    const response = await api.fetchApi(path, {signal: AbortSignal.timeout(5000)});
    if (!response.ok) throw Error(`HTTP ${response.status}`);
    return response.json();
  }
  async function settle(run, kind, detail = "", history = null) {
    if (active !== run || run.settling) return;
    run.settling = true;
    try {
      if (kind !== "success") { finish(run, kind, detail); return; }
      // Cached nodes may emit no fresh onExecuted callback. Recover only this
      // prompt's report, never any report persisted on the saved node.
      if (!run.report) {
        history ||= (await read(`/history/${encodeURIComponent(run.id)}`))[run.id];
        run.report = reportFrom(history?.outputs?.[String(node.id)]);
      }
      if (run.dirty || fingerprint(await snapshot()) !== run.fingerprint) finish(run, "stale");
      else if (run.report) finish(run, "reported", "", run.report);
      else finish(run, "no_report");
    } catch (error) { finish(run, "unknown", String(error.message || error)); }
  }
  function event(run, type, data) {
    if (active !== run) return;
    if (!run.id) {
      // Bounded to this pending submission, released as soon as POST resolves.
      if (run.early.length < 256) run.early.push([type, data]);
      return;
    }
    if (String(data?.prompt_id) !== run.id) return;
    if (type === "execution_start") emit("running");
    if (type === "executed" && String(data.node) === String(node.id)) run.report = reportFrom(data.output);
    if (type === "execution_error") void settle(run, "failed", data.exception_message || "");
    if (type === "execution_interrupted") void settle(run, "interrupted");
    if (type === "execution_success") void settle(run, "success");
  }
  async function poll(run) {
    if (active !== run || run.settling) return;
    try {
      const h = (await read(`/history/${encodeURIComponent(run.id)}`))[run.id];
      if (active !== run) return;
      if (h?.status) {
        const messages = h.status.messages || [];
        const error = messages.find(([type]) => type === "execution_error");
        const interrupted = messages.some(([type]) => type === "execution_interrupted");
        await settle(run, interrupted ? "interrupted" : error || h.status.status_str === "error" ? "failed" : "success",
          error?.[1]?.exception_message || "", h);
        return;
      }
      const queue = await read("/queue");
      const running = (queue.queue_running || []).some(p => String(p[1]) === run.id);
      const pending = (queue.queue_pending || []).some(p => String(p[1]) === run.id);
      if (!running && !pending) {
        if (++run.absent >= 2) { finish(run, "unknown"); return; }
      } else { run.absent = 0; emit(running ? "running" : "queued"); }
    } catch (error) { finish(run, "unknown", String(error.message || error)); return; }
    if (active === run) run.timer = schedule(() => void poll(run), pollMs);
  }
  return {
    get busy() { return !!active; },
    repaint() { if (lastState && !disposed) onState(lastState); },
    changed() { if (active) active.dirty = true; else lastState = null; },
    async submit() {
      if (active || disposed) return;
      const run = {id: null, early: [], listeners: [], timer: null, absent: 0, dirty: false};
      active = run; emit("submitting");
      try {
        const prompt = await snapshot();
        if (active !== run) return;
        run.fingerprint = fingerprint(prompt);
        for (const type of ["execution_start", "executed", "execution_success", "execution_error", "execution_interrupted"]) {
          const listener = e => event(run, type, e.detail);
          api.addEventListener(type, listener); run.listeners.push([type, listener]);
        }
        const response = await api.queuePrompt(0, prompt);
        if (active !== run) return;
        if (!response?.prompt_id) throw Error("Missing prompt_id");
        run.id = String(response.prompt_id); emit("queued");
        for (const [type, data] of run.early) event(run, type, data);
        run.early = [];
        if (active === run && !run.settling) run.timer = schedule(() => void poll(run), pollMs);
      } catch (error) { finish(run, "submit_failed", inspectionError(error)); }
    },
    dispose() { disposed = true; if (active) cleanup(active); active = null; },
  };
}
