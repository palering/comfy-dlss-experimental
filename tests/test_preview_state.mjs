import assert from "node:assert/strict";
import test from "node:test";
import { acceptSession, previewPrompt, previewRequest, previewRangeDuration } from "../web/preview_state.js";

test("preview cards reject other nodes, workflows, old runs and stale revisions", () => {
  const current = { node_id: "5", workflow_id: "one", session_id: "b", revision: 2, created_at: 20 };
  assert.equal(acceptSession(current, { ...current, node_id: "6", revision: 3 }, "5", "one"), false);
  assert.equal(acceptSession(current, { ...current, workflow_id: "two", revision: 3 }, "5", "one"), false);
  assert.equal(acceptSession(current, { ...current, revision: 1 }, "5", "one"), false);
  assert.equal(acceptSession(current, { ...current, session_id: "a", created_at: 10 }, "5", "one"), false);
  assert.equal(acceptSession(current, { ...current, revision: 3 }, "5", "one"), true);
  assert.equal(acceptSession(current, { ...current, session_id: "c", created_at: 21, revision: 0 }, "5", "one"), true);
});

test("preview queue includes dependencies but excludes full-video export", () => {
  const graph = {
    "1": { inputs: { name: "clip.mp4" } },
    "2": { inputs: { video: ["1", 0] } },
    "3": { inputs: { intensity: 0.25 } },
    "5": { inputs: { sequence: ["2", 0], profile: ["3", 0] } },
    "6": { inputs: { sequence: ["2", 0], profile: ["3", 0] } },
    "7": { inputs: { video: ["6", 0] } },
  };
  assert.deepEqual(Object.keys(previewPrompt(graph, "5")).sort(), ["1", "2", "3", "5"]);
  assert.throws(() => previewPrompt(graph, "404"));
});

test("single-frame request preserves saved graph, look, and full render branch", () => {
  const graph = {"1": {inputs:{file:"video.mp4"}}, "6": {inputs:{sequence:["1",0],duration:2,start_time:4}},
    "7": {inputs:{video:["6",0]}}};
  const frame = previewRequest(graph, "6", "frame", .5);
  assert.equal(frame["6"].inputs.start_time, 4);
  assert.equal(frame["6"].inputs.duration, 2);
  assert.equal(frame["6"].inputs.cursor_time, .5);
  assert.equal(frame["6"].inputs.preview_mode, "frame");
  assert.equal(graph["6"].inputs.preview_mode, undefined);
  assert.equal(frame["7"], undefined);
  assert.throws(() => previewRequest(graph,"6","frame",2));
  assert.throws(() => previewRequest(graph,"6","frame",NaN));
  assert.equal(previewRequest(graph,"6","range",1)["6"].inputs.cursor_time, 0);
});

test("preview range and scale never rewrite independent Process or queue preview Save Video", () => {
  const graph = {"1": {inputs: {file: "video.mp4"}},
    "6": {inputs: {sequence: ["1", 0], start_time: 10, duration: 3, preview_scale: 50, preview_mode: "frame"}},
    "7": {inputs: {sequence: ["1", 0], start_time: 0, duration: 0, scale: 100}},
    "8": {inputs: {video: ["7", 0]}}, "9": {inputs: {video: ["6", 2]}}};
  const original = structuredClone(graph);
  const selected = previewRequest(graph, "6", "range", 1.5);
  assert.deepEqual(Object.keys(selected).sort(), ["1", "6"]);
  assert.equal(selected["6"].inputs.duration, 3);
  assert.equal(selected["6"].inputs.preview_scale, 50);
  assert.equal(selected["6"].inputs.preview_mode, "range");
  assert.deepEqual(graph, original);
});

test("to-end mode ignores the manual duration for frame requests; backend validates actual VIDEO", () => {
  const graph = {"6": {inputs: {start_time: 0, duration: 2, process_to_end: true}}};
  const selected = previewRequest(graph, "6", "frame", 35);
  assert.equal(selected["6"].inputs.cursor_time, 35);
  assert.equal(selected["6"].inputs.process_to_end, true);
  assert.equal(selected["6"].inputs.duration, 2);
  assert.equal(previewRangeDuration(2, 10, true, 96), 86);
  assert.equal(previewRangeDuration(2, 10, false, 96), 2);
  assert.equal(previewRangeDuration(2, 10, true, null), null);
  assert.throws(() => previewRequest(graph, "6", "frame", NaN));
});
