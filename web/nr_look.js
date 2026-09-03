import { app } from "../../scripts/app.js";
import { migrateLookChoices } from "./nr_look_choices.js";

app.registerExtension({
  name: "comfy-dlss-experimental.nr-look-labels",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "DLSSExperimentalNRProfile") return;
    const definitions = { ...nodeData.input?.required, ...nodeData.input?.optional };
    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (...args) {
      const result = configure?.apply(this, args);
      migrateLookChoices(this.widgets, definitions);
      return result;
    };
  },
});
