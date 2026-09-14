import { app } from "../../scripts/app.js";
import { t, onLocaleChange } from "./i18n.js";

const rangeNodes = new Set(["DLSSExperimentalPipelineRender", "DLSSExperimentalProcessVideo",
  "DLSSExperimentalPipelinePreview", "DLSSExperimentalPreviewSession"]);

export function controlPresentation(type, values, linked = false) {
  const hidden = [];
  if (linked) return {hidden, message:"模式由连线控制；本地值仅供参考，实际范围和尺寸以运行报告为准。"};
  if (type === "DLSSExperimentalStreamlineStage") {
    if (values.mode === "dlaa") return {hidden:["output_width", "output_height", "output_size_mode"],
      message:"DLAA 保持输入尺寸，忽略手动宽高。切回 SR 会恢复原值，不会自动更改参数。"};
    if (values.output_size_mode && values.output_size_mode !== "manual") return {hidden:["output_width", "output_height"],
      message:"SR 按倍率从当前输入自动计算宽高，换视频后重新计算；保持比例并对齐偶数尺寸，实际尺寸见运行报告。质量模式仍须通过运行时检查。Pipeline Render 尺寸百分比保持 100。"};
    return {hidden, message:"SR 使用本阶段的输出宽高；Pipeline Render 的尺寸百分比必须为 100。参数更改后需重新运行，旧报告不会自动更新。"};
  }
  if (values.process_to_end === true) return {hidden:["duration"],
    message:"处理到末尾：忽略手动时长，保留起点；起点 0 才是全视频。单帧模式仍只输出一帧，范围以执行时读取的输入为准。"};
  return {hidden, message:"手动区间：从起点处理指定时长，遇末尾缩短。正式输出节点时长 0 也表示剩余全部；预览与正式输出的范围互相独立。"};
}

app.registerExtension({
  name:"comfy-dlss-experimental.conditional-controls",
  beforeRegisterNodeDef(Node, data) {
    if (data.name !== "DLSSExperimentalStreamlineStage" && !rangeNodes.has(data.name)) return;
    const previous = Node.prototype.onNodeCreated;
    Node.prototype.onNodeCreated = function () {
      previous?.apply(this, arguments);
      const node = this;
      const widgets = Object.fromEntries((node.widgets || []).map(w => [w.name, w]));
      const names = data.name === "DLSSExperimentalStreamlineStage" ? ["output_width", "output_height", ...(widgets.output_size_mode ? ["output_size_mode"] : [])] : ["duration"];
      const controller = widgets[data.name === "DLSSExperimentalStreamlineStage" ? "mode" : "process_to_end"];
      if (!controller || names.some(name => !widgets[name])) return;
      const originals = new Map(names.map(name => [name, {type:widgets[name].type, computeSize:widgets[name].computeSize}]));
      const help = document.createElement("div");
      help.className = "dlss-conditional-help";
      help.style.cssText = "box-sizing:border-box;width:100%;height:100%;padding:8px 10px;overflow:auto;background:#17232d;color:#e4edf3;font:12px/1.5 system-ui";
      const hint = node.addDOMWidget("dlss_conditional_help", "dlss_conditional_help", help, {serialize:false});
      hint.computeSize = () => [300, 96];
      function render() {
        if (widgets.output_size_mode && (widgets.output_size_mode.value === "" || widgets.output_size_mode.value == null)) widgets.output_size_mode.value = "manual";
        const linked = node.inputs?.some(i => [controller.name,"output_size_mode"].includes(i.name) && i.link != null);
        const view = controlPresentation(data.name, Object.fromEntries(Object.entries(widgets).map(([key,w]) => [key,w.value])), linked);
        for (const name of names) {
          const w = widgets[name], original = originals.get(name);
          w.hidden = view.hidden.includes(name); w.options ??= {}; w.options.hidden = w.hidden;
          if (typeof node.isWidgetVisible === "function") continue;
          w.type = w.hidden ? "hidden" : original.type;
          if (w.hidden) w.computeSize = () => [0,-4];
          else if (original.computeSize) w.computeSize = original.computeSize;
          else delete w.computeSize;
        }
        help.textContent = t(view.message); help.title = help.textContent;
        const min = node.computeSize();
        node.setSize([Math.max(node.size[0], min[0], 340), Math.max(node.size[1], min[1])]);
        node.setDirtyCanvas(true, true);
      }
      const callback = controller.callback;
      controller.callback = function () {const result=callback?.apply(this,arguments);render();return result;};
      if (widgets.output_size_mode) {
        const old = widgets.output_size_mode.callback;
        widgets.output_size_mode.callback = function () {const result=old?.apply(this,arguments);render();return result;};
      }
      node.dlssConditionalControls = {render, dispose:onLocaleChange(render)};
      render();
    };
    for (const hook of ["onConfigure", "onWidgetChanged", "onConnectionsChange"]) {
      const old = Node.prototype[hook];
      Node.prototype[hook] = function () {const result=old?.apply(this,arguments);this.dlssConditionalControls?.render();return result;};
    }
    const removed = Node.prototype.onRemoved;
    Node.prototype.onRemoved = function () {this.dlssConditionalControls?.dispose();return removed?.apply(this,arguments);};
  },
});
