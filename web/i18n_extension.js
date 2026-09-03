import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { setLocale, getLocale, onLocaleChange } from "./i18n.js";
import { applyOptionLabels } from "./i18n_options.js";

let catalogs = {};
const nodes = new Set();
function updateOptions() {
  const catalog = catalogs[getLocale()]?.nodeDefs;
  for (const node of nodes) applyOptionLabels(node, catalog);
}

app.registerExtension({
  name: "comfy-dlss-experimental.i18n",
  async setup() {
    const settings = app.ui.settings;
    setLocale(settings.getSettingValue("Comfy.Locale", "en"));
    settings.addEventListener("Comfy.Locale.change", event => setLocale(event.detail.value));
    onLocaleChange(updateOptions);
    try {
      // Same official catalogs used by standard node titles and tooltips.
      const response = await api.fetchApi("/i18n");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      catalogs = await response.json();
      updateOptions();
    } catch (error) {
      // Localization must not prevent node loading or change execution values.
      console.warn("[DLSS] Combo translations unavailable; retaining original labels", error);
    }
  },
  nodeCreated(node) {
    if (!(node.comfyClass || node.type || "").startsWith("DLSSExperimental")) return;
    nodes.add(node);
    applyOptionLabels(node, catalogs[getLocale()]?.nodeDefs);
    const configure = node.onConfigure;
    node.onConfigure = function (...args) {
      const result = configure?.apply(this, args);
      applyOptionLabels(this, catalogs[getLocale()]?.nodeDefs);
      return result;
    };
    const remove = node.onRemoved;
    node.onRemoved = function (...args) {
      nodes.delete(this);
      return remove?.apply(this, args);
    };
  },
});
