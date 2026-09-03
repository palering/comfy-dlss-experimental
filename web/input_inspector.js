import { t, bindText, disposeTranslations, onLocaleChange } from "./i18n.js";
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { previewPrompt } from "./preview_state.js";
import { reportSections, reportIssue } from "./input_report.js";

const NODE = "DLSSExperimentalPrepareTemporalSequence";
const states = () => ({ready: t("可开始准备"), ready_with_assumptions: t("可准备 · 含用户假设"),
  needs_confirmation: t("需要确认色彩解释"), unsupported: t("当前不支持"), unreadable: t("无法读取"),
  deferred: t("需物化后检查")});
function element(tag, className, value) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (typeof value === "function") bindText(result, value);
  else if (value !== undefined) result.textContent = value;
  return result;
}
function installStyle() {
  if (document.getElementById("dlss-input-style-v2")) return;
  const style = element("style");
  style.id = "dlss-input-style-v2";
  style.textContent = `
    .dlss-input-card { box-sizing:border-box; width:100%; min-width:0; max-width:100%;
      height:100%; min-height:0; max-height:550px; contain:inline-size;
      overflow:auto; padding:12px; color:var(--fg-color,#eee); background:#151a22;
      border:1px solid #394455; border-radius:9px; font:12px/1.5 system-ui,sans-serif; }
    .dlss-input-card * { box-sizing:border-box; min-width:0; overflow-wrap:anywhere; }
    .dlss-input-card header { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }
    .dlss-input-card button { max-width:100%; white-space:normal; margin:8px 0; padding:6px 12px; background:#233748; color:#cceaff;
      border:1px solid #45677e; border-radius:5px; cursor:pointer; }
    .dlss-input-card button:disabled { opacity:.5; cursor:wait; }
    .dlss-input-status { color:#86d5b5; }
    .dlss-input-status[data-state=needs_confirmation], .dlss-input-status[data-state=ready_with_assumptions] { color:#f2cb7f; }
    .dlss-input-status[data-state=unsupported], .dlss-input-status[data-state=unreadable] { color:#ffa7a7; }
    .dlss-input-caption { font-size:11px; color:#aab3c0; overflow-wrap:anywhere; }
    .dlss-input-card details { border-top:1px solid #303945; margin-top:8px; padding-top:6px; }
    .dlss-input-card summary { cursor:pointer; font-weight:600; }
    .dlss-input-card dl { display:grid; grid-template-columns:minmax(0,32%) minmax(0,1fr); gap:4px 10px; margin:8px 0; }
    .dlss-input-card dt { color:#aab3c0; } .dlss-input-card dd { margin:0; overflow-wrap:anywhere; }
    .dlss-input-issues { color:#f3c78e; white-space:pre-wrap; overflow-wrap:anywhere; }
  `;
  document.head.append(style);
}
app.registerExtension({
  name: "comfy-dlss-experimental.input-inspector",
  nodeCreated(node) {
    if ((node.comfyClass || node.type) !== NODE || node.__dlssInputAttached) return;
    node.__dlssInputAttached = true;
    installStyle();
    const root = element("section", "dlss-input-card");
    const header = element("header");
    const status = element("span", "dlss-input-status", () => t("尚未检查"));
    header.append(element("strong", "", () => t("视频输入诊断")), status);
    const button = element("button", "", () => t("检查输入（不运行 NR）"));
    button.type = "button";
    const caption = element("div", "dlss-input-caption", () => t("只执行本节点及上游。更换视频或配置后请重新检查。"));
    const issues = element("div", "dlss-input-issues");
    const body = element("div");
    root.append(header, button, caption, issues, body);
    root.addEventListener("pointerdown", event => event.stopPropagation());
    root.addEventListener("wheel", event => event.stopPropagation());
    let report = null, removed = false, dirty = false;
    function show(value) {
      if (!value || removed) return;
      report = value;
      dirty = false;
      node.properties ||= {};
      node.properties.dlss_input_report = value;
      status.textContent = states()[value.state] || value.state;
      status.dataset.state = value.state;
      caption.textContent = t("上次检查：") + (value.checked_at ? new Date(value.checked_at).toLocaleString() : t("未知")) + t("。输入变化后请重新检查；本结果不是整段解码保证。");
      issues.textContent = [...(value.issues || []).map(x => reportIssue(x, value)), ...(value.warnings || []).map(t)].join("\n");
      body.replaceChildren();
      for (const [index, [title, rows]] of reportSections(value).entries()) {
        const details = element("details");
        details.open = index === 0 || index === 2 || (Boolean(value.media_tools) && index === 6);
        details.append(element("summary", "", title));
        const list = element("dl");
        for (const [key, content] of rows) list.append(element("dt", "", key), element("dd", "", content));
        details.append(list); body.append(details);
      }
      button.disabled = false;
    }
    button.onclick = async () => {
      button.disabled = true; status.textContent = t("检查排队中");
      try {
        const graph = await app.graphToPrompt();
        if (!Object.hasOwn(graph.output, String(node.id))) throw new Error(t("子图内部请使用 Comfy 执行到所选节点。"));
        await api.queuePrompt(0, {workflow: graph.workflow, output: previewPrompt(graph.output, node.id)});
      } catch (error) {
        status.textContent = t("提交失败"); issues.textContent = error.message || String(error);
      } finally { button.disabled = false; }
    };
    node.addDOMWidget("dlss_input_inspector", "DLSS_INPUT_INSPECTOR", root, {
      serialize:false, hideOnZoom:false, getMinHeight:() => 420, getMaxHeight:() => 570,
    });
    node.setSize([Math.max(node.size[0], 500), Math.max(node.size[1], 670)]);
    const oldExecuted = node.onExecuted;
    node.onExecuted = function(output, ...rest) {
      for (const value of output?.dlss_input_report || []) show(value);
      return oldExecuted?.call(this, output, ...rest);
    };
    const oldConfigure = node.onConfigure;
    node.onConfigure = function(...args) {
      const result = oldConfigure?.apply(this, args);
      show(node.properties?.dlss_input_report);
      return result;
    };
    const oldChanged = node.onWidgetChanged;
    node.onWidgetChanged = function(...args) {
      if (report) { dirty = true; status.textContent = t("配置已更改，请重新检查"); }
      return oldChanged?.apply(this, args);
    };
    const oldRemoved = node.onRemoved;
    const unsubscribe = onLocaleChange(() => {
      if (!report) return;
      const wasDirty = dirty;
      const open = [...body.children].map(item => item.open);
      show(report);
      if (wasDirty) { dirty = true; status.textContent = t("配置已更改，请重新检查"); }
      [...body.children].forEach((item, index) => { item.open = open[index] ?? item.open; });
    });
    node.onRemoved = function(...args) { removed = true; unsubscribe(); disposeTranslations(root); return oldRemoved?.apply(this, args); };
  },
});
