// Pure state helpers: host is supplied by Comfy's server, never navigator.platform.
export function visibleRuntimeFields(values, host) {
  const platform = values.system_platform === "auto" || !values.system_platform ? host : values.system_platform;
  const linux = platform === "linux";
  const proton = linux && (values.linux_execution || "proton") === "proton";
  return {linux_execution: linux, proton, proton_path: proton, linux_display_backend: proton};
}

// Keep hidden widgets in serialization and restore their original implementations.
const original = new WeakMap();
export function setRuntimeWidgetVisible(widget, visible) {
  if (!original.has(widget)) original.set(widget, {type: widget.type, computeSize: widget.computeSize,
    hidden: widget.hidden, optionsHidden: widget.options?.hidden});
  const saved = original.get(widget);
  widget.hidden = !visible;
  if (widget.options) widget.options.hidden = !visible;
  widget.type = visible ? saved.type : "dlss_platform_hidden";
  widget.computeSize = visible ? saved.computeSize : () => [0, -4];
}
