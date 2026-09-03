// Display-only adapter for Comfy frontends that do not apply nodeDefs combo
// translations themselves. The catalogs remain the single source of truth.
export function applyOptionLabels(node, catalog) {
  const definition = catalog?.[node.comfyClass || node.type];
  if (!definition) return;
  for (const widget of node.widgets || []) {
    const labels = definition.inputs?.[widget.name]?.options;
    if (!labels || widget.type !== "combo") continue;
    // Preserve the options object shared with Nodes 2.0's widget bridge. Replace
    // only its display callback; selected values and option arrays stay intact.
    widget.options ??= {};
    widget.options.getOptionLabel = value => {
        const key = String(value ?? "").replaceAll(".", "_");
        return Object.hasOwn(labels, key) ? labels[key] : String(value ?? "");
    };
  }
  node.setDirtyCanvas?.(true, true);
}
