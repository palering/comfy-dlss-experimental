// Migration changes captions only; values unknown to this schema stay visible
// and fail backend validation instead of silently selecting a different look.
export function migrateLookChoices(widgets, inputDefinitions) {
  for (const widget of widgets || []) {
    const metadata = inputDefinitions[widget.name]?.[1];
    if (typeof widget.value !== "number" || !Number.isInteger(widget.value)) continue;
    const label = metadata?.dlss_legacy_labels?.[String(widget.value)];
    if (label !== undefined && metadata.options?.includes(label)) widget.value = label;
  }
}
