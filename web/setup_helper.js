import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { t, bindText, disposeTranslations, onLocaleChange } from "./i18n.js";
import { copyDiagnosticText } from "./preview_diagnostics.js";
import { createInspectionTask, inspectionMessage } from "./inspection_task.mjs?v=1";
import { decodeDiagnosticReport } from "./diagnostic_report.js";

const NODE = "DLSSExperimentalSetupHelper";

function element(tag, className, value) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (typeof value === "function") bindText(result, value);
  else if (value !== undefined) result.textContent = value;
  return result;
}

function installStyle() {
  if (document.querySelector("link[data-dlss-setup-style]")) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.dataset.dlssSetupStyle = "1";
  link.href = new URL("./setup_helper.css?v=1", import.meta.url).href;
  document.head.append(link);
}

function statusText(value) {
  return value?.ready ? t("配置可用") : t("配置不完整");
}

app.registerExtension({
  name: "comfy-dlss-experimental.setup-helper",
  nodeCreated(node) {
    if ((node.comfyClass || node.type) !== NODE || node.__dlssSetupAttached) return;
    node.__dlssSetupAttached = true;
    installStyle();
    const root = element("section", "dlss-setup-card");
    const header = element("header");
    const status = element("strong", "dlss-setup-status", t("尚未检查"));
    status.setAttribute("role", "status");
    const counts = element("span", "dlss-setup-counts");
    header.append(status, counts);
    const actions = element("div", "dlss-setup-actions");
    const run = element("button", "", () => t("检查所选配置"));
    const copy = element("button", "", () => t("复制完整报告"));
    run.type = copy.type = "button";
    copy.disabled = true;
    actions.append(run, copy);
    const caption = element("p", "dlss-setup-caption", () => t("只进行依赖预检；不会启动 NR Worker 或加载 NR 模型。"));
    const copyText = element("textarea", "dlss-setup-copy");
    copyText.readOnly = true;
    copyText.hidden = true;
    copyText.rows = 7;
    bindText(copyText, () => t("任务详情（自动复制受限时，可全选后手动复制）"), "ariaLabel");
    copyText.addEventListener("keydown", event => event.stopPropagation());
    copyText.addEventListener("keyup", event => event.stopPropagation());
    const list = element("div", "dlss-setup-list");
    root.append(header, actions, caption, copyText, list);
    root.addEventListener("pointerdown", event => event.stopPropagation());
    root.addEventListener("wheel", event => event.stopPropagation());

    let report = null, dirty = false;
    let removed = false;
    function show(value) {
      value = decodeDiagnosticReport(value);
      if (!value || removed) return;
      report = value;
      dirty = false;
      list.hidden = false;
      node.properties ||= {};
      node.properties.dlss_setup_report = value;
      status.textContent = statusText(value);
      status.dataset.ready = value.ready ? "true" : "false";
      const summary = value.summary || {};
      counts.textContent = `${t("通过")} ${summary.pass || 0} · ${t("提醒")} ${summary.warn || 0} · ${t("失败")} ${summary.fail || 0}`;
      caption.textContent = `${t("上次检查：")}${value.checked_at ? new Date(value.checked_at).toLocaleString() : t("未知")}。 ${t("只进行依赖预检；不会启动 NR Worker 或加载 NR 模型。")}`;
      list.replaceChildren();
      for (const check of value.checks || []) {
        const row = element("details", "dlss-setup-check");
        row.dataset.status = check.status;
        row.open = check.status === "fail";
        const summaryElement = element("summary");
        summaryElement.append(element("span", "dlss-setup-dot", "●"), element("span", "", check.label || check.id));
        row.append(summaryElement, element("pre", "", check.detail || ""));
        list.append(row);
      }
      copy.disabled = false;
    }

    const task = createInspectionTask({api, app, node, reportKeys: ["dlss_setup_report"], onReport: show,
      onState(value) {
        run.disabled = value.busy;
        if (value.kind === "reported") return;
        status.textContent = inspectionMessage(value); delete status.dataset.ready;
        copy.disabled = true;
        list.hidden = !!report; counts.textContent = report ? t("旧报告已隐藏，不代表本次检查结果。") : "";
      }});
    run.onclick = () => task.submit();
    copy.onclick = async () => {
      if (!report) return;
      copyText.hidden = true;
      copyText.value = "";
      const result = await copyDiagnosticText(JSON.stringify(report, null, 2), {
        clipboard: navigator.clipboard, document, textbox: copyText,
      });
      copy.textContent = result === "copied" ? t("已复制，可直接粘贴发送")
        : result === "manual" ? t("自动复制受限，详情已选中，请按 ⌘C / Ctrl+C") : t("复制失败");
      if (result === "copied") setTimeout(() => { if (!removed) copy.textContent = t("复制完整报告"); }, 1200);
    };

    node.addDOMWidget("dlss_setup_helper", "DLSS_SETUP_HELPER", root, {
      serialize: false, hideOnZoom: false, getMinHeight: () => 260, getMaxHeight: () => 620,
    });
    node.setSize([Math.max(node.size[0], 500), Math.max(node.size[1], 560)]);
    const executed = node.onExecuted;
    node.onExecuted = function(output, ...rest) {
      if (!task.busy) for (const value of output?.dlss_setup_report || []) { task.changed(); show(value); }
      return executed?.call(this, output, ...rest);
    };
    const configure = node.onConfigure;
    node.onConfigure = function(...args) {
      const result = configure?.apply(this, args);
      show(node.properties?.dlss_setup_report);
      return result;
    };
    const changed = node.onWidgetChanged;
    node.onWidgetChanged = function(...args) {
      task.changed();
      if (report) { dirty = true; status.textContent = t("配置已更改，请重新检查"); }
      return changed?.apply(this, args);
    };
    const unsubscribe = onLocaleChange(() => {
      const wasDirty = dirty; show(report);
      if (wasDirty) { dirty = true; status.textContent = t("配置已更改，请重新检查"); }
      task.repaint();
    });
    const removedHandler = node.onRemoved;
    node.onRemoved = function(...args) {
      removed = true;
      task.dispose();
      unsubscribe();
      disposeTranslations(root);
      return removedHandler?.apply(this, args);
    };
  },
});
