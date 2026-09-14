import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { onLocaleChange } from "./i18n.js";
import { copyDiagnosticText } from "./preview_diagnostics.js";
import { createInspectionTask, inspectionMessage } from "./inspection_task.mjs?v=1";
import { PIPELINE_CARD_NODES, pipelineLabels, pipelineRows } from "./pipeline_report.js";
import { decodeDiagnosticReport } from "./diagnostic_report.js";

app.registerExtension({
  name: "comfy-dlss-experimental.pipeline",
  nodeCreated(node) {
    if (!PIPELINE_CARD_NODES.has(node.comfyClass || node.type) || node.__dlssPipelineCard) return;
    node.__dlssPipelineCard = true;
    if (!document.getElementById("dlss-pipeline-style")) {
      const style = document.createElement("style");
      style.id = "dlss-pipeline-style";
      style.textContent = `
        .dlss-pipeline-card {box-sizing:border-box;contain:inline-size;min-width:0;max-width:100%;width:100%;height:100%;min-height:0;
          overflow:auto;padding:10px;background:#151a22;color:#eee;border:1px solid #394455;border-radius:8px;font:12px/1.5 system-ui;}
        .dlss-pipeline-card * {box-sizing:border-box;min-width:0;max-width:100%;overflow-wrap:anywhere;}
        .dlss-pipeline-card nav {display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;}
        .dlss-pipeline-card button {white-space:normal;color:#d2e7f9;background:#233748;border:1px solid #45677e;border-radius:4px;padding:5px 8px;}
        .dlss-pipeline-card dl {display:grid;grid-template-columns:minmax(0,40%) minmax(0,60%);gap:4px;}
        .dlss-pipeline-card dd {margin:0;}
        .dlss-pipeline-card pre {white-space:pre-wrap;user-select:text;}
        .dlss-pipeline-card textarea {width:100%;min-height:120px;user-select:text;}
      `;
      document.head.append(style);
    }
    const el = tag => document.createElement(tag);
    const root = el("section"), title = el("strong"), status = el("p"), actions = el("nav");
    root.className = "dlss-pipeline-card";
    status.setAttribute("aria-live", "polite");
    const refresh = el("button"), copy = el("button"), note = el("p"), rows = el("dl");
    refresh.type = copy.type = "button";
    const details = el("details"), summary = el("summary"), raw = el("pre"), textbox = el("textarea");
    textbox.readOnly = true; textbox.hidden = true;
    details.append(summary, raw); actions.append(refresh, copy);
    root.append(title, status, actions, note, rows, details, textbox);
    root.addEventListener("pointerdown", event => event.stopPropagation());
    root.addEventListener("wheel", event => event.stopPropagation());
    let report = null, removed = false, dirty = false;
    function update() {
      if (removed) return;
      const l = pipelineLabels();
      title.textContent = l.title; refresh.textContent = l.refresh; copy.textContent = l.copy;
      note.textContent = `${l.note} ${l.history}`; summary.textContent = l.details;
      textbox.setAttribute("aria-label", l.details);
      status.textContent = dirty ? l.stale : report ? (report.state === "blocked" ? l.blocked : l.configured) : l.empty;
      copy.disabled = !report;
      rows.replaceChildren();
      if (report) for (const [key, value] of pipelineRows(report)) {
        const dt = el("dt"), dd = el("dd"); dt.textContent = key; dd.textContent = value;
        rows.append(dt, dd);
      }
      raw.textContent = report ? JSON.stringify(report, null, 2) : "";
      rows.hidden = false; details.hidden = false;
    }
    const task = createInspectionTask({api, app, node, reportKeys: ["dlss_pipeline_report", "dlss_sr_plan"],
      onReport(value) { report = value; dirty = false; update(); },
      onState(value) {
        refresh.disabled = value.busy;
        if (value.kind === "reported") return;
        status.textContent = inspectionMessage(value); rows.hidden = true; details.hidden = true; copy.disabled = true;
      }});
    refresh.onclick = () => task.submit();
    copy.onclick = async () => {
      if (!report) return;
      const result = await copyDiagnosticText(JSON.stringify(report, null, 2), {
        clipboard: navigator.clipboard, document, textbox,
      });
      if (!removed) status.textContent = result === "copied" ? pipelineLabels().copied : pipelineLabels().manual;
    };
    const executed = node.onExecuted;
    node.onExecuted = function(output, ...rest) {
      const incoming = decodeDiagnosticReport(output?.dlss_pipeline_report?.[0] || output?.dlss_sr_plan?.[0]);
      if (!task.busy && (incoming?.kind === "media_pipeline_report" || incoming?.kind === "sr_input_plan")) {
        task.changed();
        report = incoming; dirty = false; update();
      }
      return executed?.call(this, output, ...rest);
    };
    for (const method of ["onWidgetChanged", "onConnectionsChange"]) {
      const old = node[method];
      node[method] = function(...args) { task.changed(); if (report) { dirty = true; update(); task.repaint(); } return old?.apply(this, args); };
    }
    const unsubscribe = onLocaleChange(() => { update(); task.repaint(); }), previousRemoved = node.onRemoved;
    node.onRemoved = function(...args) { removed = true; task.dispose(); unsubscribe(); return previousRemoved?.apply(this, args); };
    node.addDOMWidget("dlss_pipeline", "DLSS_PIPELINE", root, {
      serialize:false, hideOnZoom:false, getMinHeight:() => 260, getMaxHeight:() => 2000,
    });
    node.setSize([Math.max(node.size[0], 420), Math.max(node.size[1], 520)]);
    update();
  },
});
