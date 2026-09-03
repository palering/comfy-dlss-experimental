// Pure helpers, independently testable without Comfy or a browser.
export function acceptSession(current, incoming, nodeId, workflowId = "") {
  if (!incoming || String(incoming.node_id) !== String(nodeId)) return false;
  if (workflowId && incoming.workflow_id && workflowId !== incoming.workflow_id) return false;
  if (!current) return true;
  if (incoming.session_id === current.session_id) return Number(incoming.revision) > Number(current.revision);
  return Number(incoming.created_at) > Number(current.created_at);
}

export function previewPrompt(output, targetId) {
  const selected = {};
  const visit = (id) => {
    id = String(id);
    if (Object.hasOwn(selected, id)) return;
    const node = output[id];
    if (!node) throw new Error(`Preview dependency ${id} is missing`);
    selected[id] = node;
    for (const value of Object.values(node.inputs || {})) {
      if (Array.isArray(value) && value.length === 2 && Number.isInteger(value[1]) && Object.hasOwn(output, String(value[0]))) visit(value[0]);
    }
  };
  visit(targetId);
  return selected;
}

export function previewRequest(output, targetId, mode, cursor = 0) {
  if (!["range", "frame"].includes(mode)) throw new Error("Unknown preview mode");
  const selected = previewPrompt(output, targetId);
  const target = selected[String(targetId)];
  const duration = Number(target.inputs.duration);
  if (!Number.isFinite(cursor) || cursor < 0 || (mode === "frame" && target.inputs.process_to_end !== true && cursor >= duration)) {
    throw new Error("游标必须位于预览区间内");
  }
  // Never change the saved graph or upstream nodes when choosing a quick frame.
  selected[String(targetId)] = { ...target, inputs: { ...target.inputs,
    preview_mode: mode, cursor_time: mode === "frame" ? cursor : 0 } };
  return selected;
}

export function previewRangeDuration(duration, start, toEnd, total) {
  if (!toEnd) return Number.isFinite(duration) && duration > 0 ? duration : null;
  return Number.isFinite(total) && total > start ? total - start : null;
}
