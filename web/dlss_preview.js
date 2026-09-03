import { t, bindText, disposeTranslations, onLocaleChange } from "./i18n.js";
import { stages } from "./runtime_monitor_state.js";
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { acceptSession, previewRequest, previewRangeDuration } from "./preview_state.js";
import { copyDiagnosticText, formatPreviewDiagnostics } from "./preview_diagnostics.js";
import { previewOutputNotice, previewParameterHelp } from "./preview_help.js";

const PREVIEW_NODE = "DLSSExperimentalPreviewSession";
const cards = new Set();

function element(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (typeof text === "function") bindText(value, text);
  else if (text !== undefined) value.textContent = text;
  return value;
}
function stylesheet() {
  if (document.querySelector("link[data-dlss-preview-style]")) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  // Version the stylesheet so older scaffold layouts do not survive reloads.
  link.href = new URL("./dlss_preview.css?v=8", import.meta.url).href;
  link.dataset.dlssPreviewStyle = "true";
  document.head.appendChild(link);
}
function mediaUrl(view) {
  if (!view?.filename || !view?.type) return "";
  return api.apiURL("/view?" + new URLSearchParams({
    filename: view.filename, subfolder: view.subfolder || "", type: view.type,
  }));
}
function workflowId(node) {
  return String(node.graph?.rootGraph?.id || node.graph?.id || app.graph?.id || "");
}

function attach(node) {
  if (node.__dlssPreviewAttached) return;
  node.__dlssPreviewAttached = true;
  stylesheet();
  const state = { session: null, mode: "wipe", split: 50, showB: true, queued: false, cursor: 0 };
  const root = element("div", "dlss-node-preview-root");
  const card = element("section", "dlss-node-preview-card");
  const header = element("header", "dlss-node-preview-header");
  const heading = element("strong", "dlss-node-preview-title", () => t("DLSS A/B Preview"));
  const status = element("span", "dlss-node-preview-status", () => t("待预览"));
  status.setAttribute("aria-live", "polite");
  header.append(heading, status);
  const actions = element("div", "dlss-node-preview-actions");
  const frameQueue = element("button", "dlss-node-preview-button is-primary", () => t("渲染当前帧"));
  bindText(frameQueue, () => t("只输出起点 + 游标偏移处的一帧，仍处理前置历史；不执行下游保存节点。运动效果请渲染片段。"), "title");
  const queue = element("button", "dlss-node-preview-button", () => t("渲染片段"));
  bindText(queue, () => t("从预览起点生成指定区间长度的片段，使用预览尺寸；不执行下游保存节点。"), "title");
  const cancel = element("button", "dlss-node-preview-button", () => t("取消"));
  frameQueue.type = queue.type = cancel.type = "button";
  actions.append(frameQueue, queue, cancel);
  const modes = element("div", "dlss-node-preview-modes");
  const modeButtons = new Map();
  modes.setAttribute("role", "group"); bindText(modes, () => t("对比方式"), "ariaLabel");
  for (const [key, label] of [["wipe", "滑动对比"], ["side", "并排"], ["flicker", "交替"], ["difference", "差异"]]) {
    const button = element("button", "dlss-node-preview-mode", () => t(label));
    button.type = "button";
    button.onclick = () => { state.mode = key; update(); };
    modeButtons.set(key, button);
    modes.append(button);
  }
  const viewer = element("div", "dlss-node-preview-viewer");
  const videoA = element("video", "dlss-node-preview-video preview-a");
  const videoB = element("video", "dlss-node-preview-video preview-b");
  for (const video of [videoA, videoB]) {
    video.muted = true;
    video.playsInline = true;
    video.preload = "metadata";
  }
  const labelA = element("span", "dlss-node-preview-corner label-a", "A · Original");
  const labelB = element("span", "dlss-node-preview-corner label-b", "B · NR");
  const divider = element("div", "dlss-node-preview-divider");
  divider.append(element("span", "dlss-node-preview-handle", "↔"));
  const empty = element("div", "dlss-node-preview-empty");
  const message = element("strong", "dlss-node-preview-empty-title", () => t("先看一帧，再检查运动"));
  const detail = element("span", "dlss-node-preview-empty-body", () => t("Only this node and its dependencies are queued."));
  empty.append(message, detail);
  viewer.append(videoA, videoB, labelA, labelB, divider, empty);
  const controls = element("div", "dlss-node-preview-controls");
  const play = element("button", "dlss-node-preview-button", () => t("播放"));
  play.type = "button";
  const split = element("input", "dlss-node-preview-slider");
  split.type = "range"; split.min = "0"; split.max = "100"; split.value = "50";
  bindText(split, () => t("A/B split"), "ariaLabel");
  const splitText = element("span", "dlss-node-preview-split-label", "50%");
  split.oninput = () => { state.split = Number(split.value); update(); };
  controls.append(split, splitText);
  const timeline = element("div", "dlss-node-preview-controls");
  const previous = element("button", "dlss-node-preview-button", "−1");
  const next = element("button", "dlss-node-preview-button", "+1");
  previous.type = next.type = "button";
  bindText(previous, () => t("Previous frame"), "ariaLabel"); bindText(next, () => t("Next frame"), "ariaLabel");
  const seek = element("input", "dlss-node-preview-slider");
  seek.type = "range"; seek.min = "0"; seek.max = "0"; seek.step = "1"; seek.value = "0";
  bindText(seek, () => t("预览游标"), "ariaLabel");
  const timeLabel = element("span", "dlss-node-preview-split-label", "0 / 0");
  timeline.append(play, previous, seek, next, timeLabel);
  const summary = element("div", "dlss-node-preview-summary");
  const guidance = element("div", "dlss-node-preview-guidance");
  const notice = element("p", "dlss-node-preview-notice", previewOutputNotice);
  const rangeNotice = element("p", "dlss-node-preview-notice");
  const help = element("details", "dlss-node-preview-help");
  help.append(element("summary", "", () => t("参数与保存说明")));
  const definitions = element("dl", "");
  function renderHelp() {
    definitions.replaceChildren();
    for (const [label, explanation] of previewParameterHelp()) {
      definitions.append(element("dt", "", label), element("dd", "", explanation));
    }
  }
  renderHelp();
  help.append(definitions); guidance.append(rangeNotice, notice, help);
  const diagnostics = element("details", "dlss-node-preview-diagnostics");
  diagnostics.addEventListener("toggle", () => card.classList.toggle("has-diagnostics", diagnostics.open));
  diagnostics.append(element("summary", "", () => t("本次任务详情")));
  const copyActions = element("div", "dlss-node-preview-controls dlss-node-preview-copy-actions");
  const copy = element("button", "dlss-node-preview-button", () => t("复制任务详情"));
  copy.type = "button";
  bindText(copy, () => t("复制本次执行的参数、耗时、缓存和环境信息（含本地路径，公开分享前请检查）"), "title");
  const copyStatus = element("span", "dlss-node-preview-copy-status");
  copyStatus.setAttribute("role", "status");
  const copyText = element("textarea", "dlss-node-preview-copy-text");
  copyText.readOnly = true; copyText.hidden = true; copyText.rows = 7;
  bindText(copyText, () => t("任务详情（自动复制受限时，可全选后手动复制）"), "ariaLabel");
  copyText.addEventListener("keydown", event => event.stopPropagation());
  copyText.addEventListener("keyup", event => event.stopPropagation());
  let copying = false;
  copy.onclick = async () => {
    if (!state.session || copying) return;
    // Take a snapshot before clipboard permission prompts or a new session arrives.
    const session = state.session;
    copying = true; copy.disabled = true; copyStatus.textContent = t("正在复制…");
    copyText.hidden = true; copyText.value = "";
    try {
      const result = await copyDiagnosticText(formatPreviewDiagnostics(session), {
        clipboard: navigator.clipboard, document, textbox: copyText,
      });
      if (result === "copied") {
        copyStatus.textContent = t("已复制，可直接粘贴发送");
      } else if (result === "manual") {
        copyStatus.textContent = t("自动复制受限，详情已选中，请按 ⌘C / Ctrl+C");
      } else {
        copyStatus.textContent = t("复制未完成，请重试");
      }
    } catch {
      copyStatus.textContent = t("复制未完成，请重试");
    } finally {
      copying = false; copy.disabled = !state.session;
    }
  };
  copyActions.append(copy, copyStatus);
  const timing = element("pre", ""); diagnostics.append(copyActions, copyText, timing);
  card.append(header, actions, modes, viewer, controls, timeline, summary, guidance, diagnostics);
  root.append(card);
  // Keep input gestures in the card instead of dragging/panning the canvas.
  root.addEventListener("pointerdown", (event) => event.stopPropagation());
  root.addEventListener("wheel", (event) => event.stopPropagation());
  const rate = () => {
    const parts = String(state.session?.frame_rate || 24).split("/").map(Number);
    return parts.length === 2 ? parts[0] / parts[1] : parts[0];
  };
  const frameCount = () => Number(state.session?.frame_count || 0);
  const widget = (name) => node.widgets?.find((w) => w.name === name);
  const rangeDuration = () => {
    // Adapter header metadata is a UI estimate only. Execution resolves against
    // the actual input VIDEO again, including any upstream trim/replacement.
    const slot = node.inputs?.findIndex(input => input.name === "sequence");
    const adapter = slot >= 0 ? node.getInputNode?.(slot) : null;
    const total = adapter?.properties?.dlss_input_report?.active_view?.duration ?? state.session?.input_duration;
    return previewRangeDuration(Number(widget("duration")?.value || 3), Number(widget("start_time")?.value || 0),
      widget("process_to_end")?.value === true, Number.isFinite(total) ? total : null);
  };
  const cursorFrames = () => Math.max(1, Math.ceil((rangeDuration() ?? 0) * rate()));
  const cursorLabel = () => state.cursor.toFixed(2) + "s / " + (rangeDuration() === null ? t("执行时读取末尾") : rangeDuration().toFixed(2) + "s");
  const hasMedia = () => Boolean(videoA.dataset.source && videoB.dataset.source);
  function pause() {
    videoA.pause(); videoB.pause(); play.textContent = t("播放");
  }
  function setFrame(index) {
    pause();
    index = Math.max(0, Math.min(cursorFrames() - 1, index));
    state.cursor = index / rate();
    const cursorWidget = widget("cursor_time");
    if (cursorWidget) cursorWidget.value = state.cursor;
    if (state.session?.preview_mode !== "frame") {
      for (const video of [videoA, videoB]) if (video.readyState >= 1) video.currentTime = Math.min((index + 0.01) / rate(), (frameCount() - .1) / rate());
    }
    seek.value = String(index);
    timeLabel.textContent = cursorLabel();
  }
  function setSource(video, url) {
    if (video.dataset.source === url) return;
    video.pause();
    video.dataset.source = url;
    if (url) video.src = url;
    else { video.removeAttribute("src"); video.load(); }
  }
  function update() {
    const session = state.session;
    const busy = session && ["preparing", "rendering"].includes(session.state);
    const transport = session?.transport || {};
    if (session?.state === "ready") {
      setSource(videoA, mediaUrl(transport.original_view));
      setSource(videoB, mediaUrl(transport.processed_view));
    }
    queue.disabled = frameQueue.disabled = Boolean(busy || state.queued);
    cancel.disabled = !busy;
    empty.hidden = hasMedia();
    play.disabled = !hasMedia() || frameCount() <= 1;
    for (const control of [seek, previous, next]) control.disabled = Boolean(busy || state.queued || rangeDuration() === null);
    labelA.hidden = labelB.hidden = !hasMedia();
    labelA.textContent = "A · " + t(session?.labels?.a || "Original");
    labelB.textContent = "B · " + t(session?.labels?.b || "NR");
    const progress = session?.progress;
    status.textContent = state.queued ? t("已排队") : session?.state === "ready" ? t("已完成") :
      busy && progress ? (stages()[progress.stage] || t(progress.stage)) + (progress.total ? " " + progress.done + "/" + progress.total : "") :
      t(session?.state || "待预览");
    status.dataset.state = session?.state || "empty";
    message.textContent = session?.error || (busy ? t("正在准备预览…") : t("先看一帧，再检查运动"));
    detail.textContent = busy ? t("可取消本次预览；已有画面是上次结果。") :
      t("拖动下方游标定位，渲染当前帧调效果；渲染片段检查时序稳定性。");
    viewer.dataset.mode = state.mode;
    viewer.style.setProperty("--dlss-preview-split", state.split + "%");
    videoB.hidden = state.mode === "flicker" && !state.showB;
    divider.hidden = !hasMedia() || state.mode !== "wipe";
    split.hidden = state.mode !== "wipe";
    split.value = String(state.split);
    splitText.textContent = state.mode === "wipe" ? Math.round(state.split) + "%" : t(state.mode);
    for (const [key, button] of modeButtons) {
      button.classList.toggle("is-active", key === state.mode);
      button.setAttribute("aria-pressed", String(key === state.mode));
    }
    seek.max = String(cursorFrames() - 1);
    if (!hasMedia()) timeLabel.textContent = cursorLabel();
    rangeNotice.textContent = widget("process_to_end")?.value === true
      ? t("已选处理到末尾：忽略手动长度；起点 0 = 全视频。单帧按钮仍只输出一帧。范围以执行时读取的输入为准。")
      : t("手动区间：使用上方长度。要处理全部剩余时间，请勾选“处理到视频末尾”。");
    const execution = session?.execution;
    copy.disabled = !session || copying;
    summary.textContent = session ? t("本次输出 ") + Number(session.duration).toFixed(3) + "s · " + (session.source?.width || "?") + "×" +
      (session.source?.height || "?") + (session.preview_mode === "frame" ? t(" · 单帧 @ ") + Number(session.selected_start).toFixed(2) + "s" : t(" · 片段")) +
      (session.preview_mode === "frame" ? "" : t(" · 输入起点 ") + Number(session.start_time).toFixed(2) + "s") +
      (session.guide_cache_hit ? t(" · 颜色/光流缓存已复用") : "") +
      (execution ? t(" · 总耗时 ") + execution.elapsed_seconds.toFixed(2) + "s" : "") : t("节点内 A/B · SDR");
    timing.textContent = execution ? Object.entries(execution.stage_seconds || {}).map(([k, v]) => (stages()[k] || k) + ": " + v.toFixed(3) + "s").join("\n") +
      t("\n任务 ") + execution.execution_id + t("\n主机墙钟耗时，非纯 GPU 推理时间；颜色、光流和缓存写入是输入准备的子项，请勿重复相加。") : t("运行后显示准备、启动、预热、逐帧处理、编码和清理耗时。");
  }
  const binding = {
    executionError(payload) {
      if (!state.promptId || payload?.prompt_id !== state.promptId) return;
      state.queued = false;
      if (state.session && ["preparing", "rendering"].includes(state.session.state)) {
        state.session = { ...state.session, state:"failed", error:payload.exception_message || t("任务被中断") };
      }
      update(); status.textContent = t("执行未完成");
      message.textContent = payload.exception_message || t("任务被中断");
      empty.hidden = false;
    },
    apply(payload) {
      if (!acceptSession(state.session, payload, node.id, workflowId(node))) return;
      const changed = payload.session_id !== state.session?.session_id;
      state.session = payload;
      state.queued = false;
      node.properties = node.properties || {};
      node.properties.dlss_preview_session = payload.session_id;
      if (changed) pause();
      update();
    },
  };
  cards.add(binding);
  const render = async (mode) => {
    state.queued = true; update();
    try {
      const graph = await app.graphToPrompt();
      if (!Object.hasOwn(graph.output, String(node.id))) throw new Error(t("Use Comfy queue controls for previews inside subgraphs."));
      const length = rangeDuration();
      const typedCursor = Number(widget("cursor_time")?.value ?? state.cursor);
      const cursor = Math.max(0, widget("process_to_end")?.value === true || length === null
        ? typedCursor : Math.min(typedCursor, length - 1 / rate()));
      const queued = await api.queuePrompt(0, { workflow: graph.workflow, output: previewRequest(graph.output, node.id, mode, cursor) });
      state.promptId = queued.prompt_id;
      status.textContent = t("已排队");
    } catch (error) {
      message.textContent = error.message || JSON.stringify(error);
      empty.hidden = false;
      state.queued = false; queue.disabled = frameQueue.disabled = false;
    }
  };
  queue.onclick = () => render("range");
  frameQueue.onclick = () => render("frame");
  cancel.onclick = async () => {
    const id = state.session?.session_id;
    if (!id) return;
    cancel.disabled = true;
    try {
      const response = await api.fetchApi("/dlss-experimental/preview/sessions/" + encodeURIComponent(id) + "/cancel", { method: "POST" });
      if (!response.ok) throw new Error(t("Cancel request failed"));
      status.textContent = t("Cancelling");
    } catch (error) { status.textContent = error.message; cancel.disabled = false; }
  };
  play.onclick = async () => {
    if (!hasMedia()) return;
    if (!videoA.paused) { pause(); return; }
    if (videoA.ended || videoA.currentTime >= (frameCount() - 0.1) / rate()) {
      videoA.currentTime = videoB.currentTime = 0;
    } else videoB.currentTime = videoA.currentTime;
    const outcomes = await Promise.allSettled([videoA.play(), videoB.play()]);
    if (outcomes.some((outcome) => outcome.status === "rejected")) { pause(); status.textContent = t("Playback unavailable"); }
    else play.textContent = t("暂停");
  };
  for (const video of [videoA, videoB]) {
    video.addEventListener("loadedmetadata", () => { video.currentTime = 0; });
    video.addEventListener("error", () => { status.textContent = t("Media unavailable — render preview again"); });
  }
  videoA.addEventListener("ended", pause);
  videoB.addEventListener("ended", pause);
  videoA.addEventListener("timeupdate", () => {
    if (state.session?.preview_mode === "frame") return;
    if (videoB.readyState >= 2 && !videoA.paused && Math.abs(videoA.currentTime - videoB.currentTime) > 1.25 / rate()) videoB.currentTime = videoA.currentTime;
    const frame = Math.min(frameCount() - 1, Math.max(0, Math.floor(videoA.currentTime * rate() + 0.001)));
    seek.value = String(frame); timeLabel.textContent = (frame + 1) + " / " + frameCount();
    state.cursor = frame / rate();
    if (widget("cursor_time")) widget("cursor_time").value = state.cursor;
  });
  seek.oninput = () => setFrame(Number(seek.value));
  previous.onclick = () => setFrame(Number(seek.value) - 1);
  next.onclick = () => setFrame(Number(seek.value) + 1);
  const pointer = (event) => {
    const bounds = viewer.getBoundingClientRect();
    state.split = Math.max(0, Math.min(100, (event.clientX - bounds.left) / bounds.width * 100));
    update();
  };
  viewer.onpointerdown = (event) => {
    if (state.mode !== "wipe" || !hasMedia()) return;
    viewer.setPointerCapture(event.pointerId); pointer(event);
  };
  viewer.onpointermove = (event) => { if (viewer.hasPointerCapture(event.pointerId)) pointer(event); };
  const flicker = window.setInterval(() => {
    if (state.mode === "flicker") { state.showB = !state.showB; update(); }
  }, 500);
  node.addDOMWidget("dlss_preview", "DLSS_PREVIEW", root, {
    serialize: false, hideOnZoom: false, getMinHeight: () => 520, getMaxHeight: () => Number.POSITIVE_INFINITY,
  });
  node.setSize([Math.max(Number(node.size?.[0]) || 0, 580), Math.max(Number(node.size?.[1]) || 0, 820)]);
  const oldExecuted = node.onExecuted;
  node.onExecuted = function (output, ...rest) {
    for (const payload of output?.dlss_preview || []) binding.apply(payload);
    return oldExecuted?.call(this, output, ...rest);
  };
  const restore = async () => {
    const id = node.properties?.dlss_preview_session;
    if (!id) return;
    try {
      const response = await api.fetchApi("/dlss-experimental/preview/sessions/" + encodeURIComponent(id));
      if (response.ok) binding.apply(await response.json());
    } catch { /* Re-queue restores sessions after a backend restart. */ }
  };
  const oldConfigure = node.onConfigure;
  node.onConfigure = function (...args) {
    const result = oldConfigure?.apply(this, args);
    // Old saved workflows have no values for these appended optional widgets.
    const mode = widget("preview_mode"), cursor = widget("cursor_time");
    if (mode && (mode.value === undefined || mode.value === null || mode.value === "")) mode.value = "range";
    if (cursor && (cursor.value === undefined || cursor.value === null)) cursor.value = 0;
    const toEnd = widget("process_to_end");
    if (toEnd && typeof toEnd.value !== "boolean") toEnd.value = false;
    state.cursor = Number(cursor?.value || 0);
    void restore(); update(); return result;
  };
  const oldWidgetChanged = node.onWidgetChanged;
  node.onWidgetChanged = function (...args) {
    const result = oldWidgetChanged?.apply(this, args);
    state.cursor = Number(widget("cursor_time")?.value || 0);
    update(); return result;
  };
  for (const name of ["process_to_end", "start_time", "duration", "cursor_time"]) {
    const control = widget(name);
    if (!control) continue;
    const callback = control.callback;
    control.callback = function (...args) {
      const result = callback?.apply(this, args);
      queueMicrotask(() => { state.cursor = Number(widget("cursor_time")?.value || 0); update(); });
      return result;
    };
  }
  const oldRemoved = node.onRemoved;
  const unsubscribe = onLocaleChange(() => {
    renderHelp(); update();
    play.textContent = videoA.paused ? t("播放") : t("暂停");
  });
  node.onRemoved = function (...args) {
    unsubscribe(); disposeTranslations(root);
    cards.delete(binding); clearInterval(flicker); pause();
    for (const video of [videoA, videoB]) { video.removeAttribute("src"); video.load(); }
    return oldRemoved?.apply(this, args);
  };
  update();
}
app.registerExtension({
  name: "comfy-dlss-experimental.preview",
  nodeCreated(node) { if ((node.comfyClass || node.type) === PREVIEW_NODE) attach(node); },
  setup() {
    stylesheet();
    api.addEventListener("dlss.experimental.preview_session", (event) => {
      for (const card of cards) card.apply(event.detail);
    });
    for (const name of ["execution_error", "execution_interrupted"]) api.addEventListener(name, (event) => {
      for (const card of cards) card.executionError(event.detail);
    });
  },
});
