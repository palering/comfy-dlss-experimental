import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { visibleRuntimeFields, setRuntimeWidgetVisible } from "./runtime_platform_state.js";

let hostPromise;
function hostPlatform() {
  if (!hostPromise) hostPromise = api.fetchApi("/dlss-experimental/host").then(async response => {
    if (!response.ok) throw new Error("Host detection failed");
    return (await response.json()).platform;
  }).catch(() => { hostPromise = null; return null; });
  return hostPromise;
}

app.registerExtension({
  name: "comfy-dlss-experimental.runtime-platform",
  nodeCreated(node) {
    if ((node.comfyClass || node.type) !== "DLSSExperimentalRuntimeConfig") return;
    let host = null, removed = false;
    const bound = new WeakSet();
    function update() {
      if (removed) return;
      const values = Object.fromEntries((node.widgets || []).map(w => [w.name, w.value]));
      const visibility = visibleRuntimeFields(values, host);
      // Until the backend replies, don't hide controls based on a guess.
      if (host === null && (!values.system_platform || values.system_platform === "auto")) return;
      for (const widget of node.widgets || []) {
        if (widget.name in visibility) setRuntimeWidgetVisible(widget, visibility[widget.name]);
      }
      node.setDirtyCanvas?.(true, true);
    }
    function bind() {
      for (const name of ["system_platform", "linux_execution"]) {
        const widget = node.widgets?.find(w => w.name === name);
        if (!widget || bound.has(widget)) continue;
        bound.add(widget);
        const callback = widget.callback;
        widget.callback = function (...args) { const result = callback?.apply(this, args); update(); return result; };
      }
      update();
    }
    bind();
    void hostPlatform().then(value => { host = value; bind(); });
    const configure = node.onConfigure;
    node.onConfigure = function (...args) { const result = configure?.apply(this, args); bind(); return result; };
    const remove = node.onRemoved;
    node.onRemoved = function (...args) { removed = true; return remove?.apply(this, args); };
  },
});
