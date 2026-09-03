import { t, bindText, disposeTranslations, onLocaleChange } from "./i18n.js";
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { canRelease, historyLines, matchingRecords, number, workerLines, residentLines } from "./runtime_monitor_state.js";

function element(tag, text) {
  const value = document.createElement(tag);
  if (typeof text === "function") bindText(value, text);
  else if (text !== undefined) value.textContent = text;
  return value;
}
function attach(node) {
  const root = element("div"); root.className = "dlss-runtime-monitor";
  const live = element("details"), history = element("details");
  const liveHeading = element("summary", () => t("Worker 实时状态"));
  const historyHeading = element("summary", () => t("执行记录 · 历史快照"));
  const refresh = element("button", () => t("立即刷新")); refresh.type = "button";
  const release = element("button", () => t("终止并释放当前 Worker")); release.type = "button"; release.disabled = true;
  release.title = t("会终止该 Worker 对应的整个 DLSS 任务；不清除视频缓存，不影响其他应用。");
  const actions = element("div"); actions.className = "dlss-runtime-actions";
  actions.append(refresh, release);
  const updateStatus = element("p", () => t("展开后自动刷新；收起或页面隐藏时暂停。"));
  updateStatus.setAttribute("role", "status");
  const releaseStatus = element("p"); releaseStatus.setAttribute("role", "status");
  const state = element("p"), current = element("pre"), device = element("details"), deviceText = element("pre");
  device.append(element("summary", () => t("GPU 整卡参考 · 包括其他应用")), deviceText);
  live.append(liveHeading, actions, state, releaseStatus, current, device);
  const list = element("div"); history.append(historyHeading, list);
  root.append(updateStatus, live, history);
  root.addEventListener("pointerdown", e => e.stopPropagation());
  root.addEventListener("wheel", e => e.stopPropagation());
  let pending = false, removed = false, releasePending = false, target = null, releasedId = null;
  let timer;
  function schedule(delay) { clearTimeout(timer); if (!removed) timer = setTimeout(update, delay); }
  async function update() {
    clearTimeout(timer);
    if (pending || removed) return;
    if ((!live.open && !history.open) || document.hidden) {
      updateStatus.textContent = t("自动刷新已暂停 · 展开状态/记录且页面可见时恢复");
      return;
    }
    pending = true;
    let next = 5000;
    try {
      const response = await api.fetchApi("/dlss-experimental/executions");
      if (!response.ok) throw new Error(t("状态读取失败 ") + response.status);
      const data = await response.json();
      if (removed) return;
      const preset = node.widgets?.find(w => w.name === "runtime_preset")?.value;
      const active = matchingRecords(data.active, preset), records = matchingRecords(data.history, preset);
      const residents = matchingRecords(data.resident?.workers || [], preset);
      // An execution can briefly be on disk while its active trace is finishing.
      const activeIds = new Set(data.active.map(record => record.execution_id));
      const completed = records.filter(record => record.state !== "running" && !activeIds.has(record.execution_id));
      next = data.active.length || residents.length ? 1000 : 5000;
      updateStatus.textContent = t`自动刷新 · ${next / 1000} 秒/次 · 更新于 ${new Date().toLocaleTimeString()}`;
      const resident = residents.find(worker => !worker.release_pending && ["idle", "running", "cleanup_failed"].includes(worker.state));
      const task = active.find(canRelease);
      target = resident ? {kind: "resident", id: resident.worker_id, mode: resident.state === "running" ? "cancel" : "idle", execution_id: resident.execution_id}
        : data.lifecycle && task ? {kind: "execution", id: task.execution_id, mode: "cancel"} : null;
      release.textContent = target?.mode === "idle" ? t("释放空闲 Worker") : t("终止并释放当前 Worker");
      release.title = target?.mode === "idle" ? t("释放这个空闲实例的 GPU 和进程资源；输入缓存保留。")
        : t("会终止该 Worker 对应的整个 DLSS 任务；不清除视频缓存，不影响其他应用。");
      release.disabled = !target || releasePending;
      state.textContent = active.length
        ? t`本预设 ${active.length} 项任务执行中 · NR 串行；下方是本任务进程组采样。`
        : residents.length ? t("本预设保留了常驻 Worker，仍占用以下资源。") : t("本预设无存活的常驻实例 · 下次实际渲染时按所选模式启动。");
      if (completed[0]?.worker_state === "cleanup_failed") state.textContent += t(" 上次清理失败，请检查执行记录；不能据此断言无残留进程。");
      state.textContent += t(" 常驻仅复用相同 NR 参数；改 Look/尺寸/运行库时重建。");
      current.textContent = [...residents.map(worker => residentLines(worker).join("\n")), ...active.map(record => workerLines(record).join("\n"))].join("\n\n") || t("没有实例可释放。历史占用请展开下方执行记录。");
      const releasedRecord = records.find(record => record.execution_id === releasedId);
      if (releasedRecord?.worker_state === "released") releaseStatus.textContent = t("该任务 Worker 已完成释放。");
      else if (releasedRecord?.worker_state === "cleanup_failed") releaseStatus.textContent = t("释放失败，请检查该任务的错误记录。");
      if (data.resident?.last_release?.worker_id === releasedId) releaseStatus.textContent = t("该常驻实例已释放。");
      deviceText.textContent = (data.gpu?.gpus || []).map(g => t`${g.name}\n整卡利用率 ${number(g.utilization_percent, "%")}\n整卡显存 ${number(g.memory_used_mib)} / ${number(g.memory_total_mib)}`).join("\n\n") || t("整卡 GPU 数据不可用");
      if (data.gpu?.sampled_at) deviceText.textContent += t`\n整卡采样于 ${new Date(data.gpu.sampled_at * 1000).toLocaleTimeString()}`;
      const openIds = new Set([...list.querySelectorAll("details[open]")].map(entry => entry.dataset.execution));
      list.replaceChildren();
      for (const record of completed.slice(0, 10)) {
        const entry = element("details"); entry.dataset.execution = record.execution_id;
        entry.open = openIds.has(record.execution_id);
        entry.append(element("summary", `${new Date(record.created_at * 1000).toLocaleTimeString()} · ${record.kind} · ${record.state} · ${number(record.elapsed_seconds, " s")}`),
          element("pre", historyLines(record).join("\n")));
        list.append(entry);
      }
      if (!completed.length) list.textContent = t("没有已完成的匹配记录；正在执行的任务只显示在实时状态中。");
    } catch (error) {
      target = null; release.disabled = true;
      updateStatus.textContent = t`${error.message} · 下次自动重试；保留画面可能已过期`;
    } finally { pending = false; schedule(next); }
  }
  release.onclick = async () => {
    const selected = target;
    if (!selected || releasePending) return;
    if (selected.mode === "cancel" && !window.confirm(t("终止当前 DLSS 任务并释放其 Worker？本次未完成的输出将不可用；输入缓存和其他程序不受影响。"))) return;
    releasePending = true; release.disabled = true;
    try {
      const path = selected.kind === "resident" ? `/dlss-experimental/workers/${encodeURIComponent(selected.id)}/release`
        : `/dlss-experimental/executions/${encodeURIComponent(selected.id)}/release-worker`;
      const response = await api.fetchApi(path, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mode: selected.mode,
          execution_id: selected.execution_id, confirm_terminate: selected.mode === "cancel"}),
      });
      const result = await response.json();
      if (response.ok && result.accepted) {
        releasedId = selected.id;
        releaseStatus.textContent = t("已请求释放；等待清理，尚未确认完成。");
      } else if (response.status === 409) releaseStatus.textContent = t("该任务已结束或没有活动 Worker，无需释放。");
      else throw new Error(t("释放请求失败 ") + response.status);
    } catch (error) { releaseStatus.textContent = error.message; }
    finally { releasePending = false; void update(); }
  };
  // Turning residency off should free its idle VRAM before upstream nodes run,
  // not only when the later NR node eventually reaches execution.
  async function retireSelectedPreset() {
    try {
      const response = await api.fetchApi("/dlss-experimental/executions");
      if (!response.ok) throw new Error(t("读取常驻状态失败"));
      const data = await response.json();
      const preset = node.widgets?.find(w => w.name === "runtime_preset")?.value;
      if (!preset) return;
      for (const worker of matchingRecords(data.resident?.workers || [], preset)) {
        const result = await api.fetchApi(`/dlss-experimental/workers/${encodeURIComponent(worker.worker_id)}/release`, {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({mode: worker.state === "running" ? "after_task" : "idle", execution_id: worker.execution_id}),
        });
        if (!result.ok && result.status !== 409) throw new Error(t("释放请求失败 ") + result.status);
        const releaseResult = await result.json();
        if (result.ok && releaseResult.accepted) {
          releasedId = worker.worker_id;
          releaseStatus.textContent = t("已关闭常驻：空闲实例将释放，执行中的任务完成后释放。");
        } else releaseStatus.textContent = t("该实例状态已变化；请检查实时状态，必要时手动释放。");
      }
    } catch (error) { releaseStatus.textContent = error.message; }
    void update();
  }
  function bindPolicyToggle() {
    const widget = node.widgets?.find(w => w.name === "keep_worker_alive");
    if (!widget || widget.__dlssResidentBound) return;
    widget.__dlssResidentBound = true;
    widget.__dlssResidentLast = widget.value;
    const previous = widget.callback;
    widget.callback = function (value, ...args) {
      const wasEnabled = widget.__dlssResidentLast === true;
      widget.__dlssResidentLast = value;
      const result = previous?.call(this, value, ...args);
      if (wasEnabled && value === false) void retireSelectedPreset();
      return result;
    };
  }
  bindPolicyToggle();
  const oldConfigure = node.onConfigure;
  node.onConfigure = function (...args) {
    const result = oldConfigure?.apply(this, args); bindPolicyToggle();
    const widget = node.widgets?.find(w => w.name === "keep_worker_alive");
    if (widget) widget.__dlssResidentLast = widget.value;
    return result;
  };
  refresh.onclick = update; live.ontoggle = update; history.ontoggle = update;
  document.addEventListener("visibilitychange", update);
  const unsubscribe = onLocaleChange(update);
  const executionChanged = () => { if (!pending) void update(); };
  api.addEventListener("executing", executionChanged);
  node.addDOMWidget("dlss_runtime_monitor", "DLSS_MONITOR", root, {
    serialize: false, getMinHeight: () => 110, getMaxHeight: () => Number.POSITIVE_INFINITY,
  });
  const oldRemoved = node.onRemoved;
  node.onRemoved = function (...args) {
    removed = true; clearTimeout(timer);
    unsubscribe(); disposeTranslations(root);
    document.removeEventListener("visibilitychange", update);
    api.removeEventListener("executing", executionChanged);
    return oldRemoved?.apply(this, args);
  };
}
app.registerExtension({name: "comfy-dlss-experimental.runtime-monitor",
  setup() {
    if (document.querySelector("link[data-dlss-runtime-style]")) return;
    const link = element("link"); link.rel = "stylesheet";
    link.href = new URL("./runtime_monitor.css?v=1", import.meta.url).href;
    link.dataset.dlssRuntimeStyle = "true"; document.head.append(link);
  },
  nodeCreated(node) { if ((node.comfyClass || node.type) === "DLSSExperimentalRuntimeConfig") attach(node); },
});
