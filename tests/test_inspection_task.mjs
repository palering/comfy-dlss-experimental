import test from "node:test";
import assert from "node:assert/strict";
import {createInspectionTask, inspectionMessage, inspectionError} from "../web/inspection_task.mjs";
import {setLocale} from "../web/i18n.js";

const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
function fixture() {
  const listeners = new Map(), timers = new Map(), states = [], reports = [], requests = [];
  let sequence = 0, posts = 0;
  const graph = {workflow: {}, output: {"1": {class_type: "Source", inputs: {file: "a.mp4"}},
    "6": {class_type: "Check", inputs: {video: ["1", 0]}}}};
  const data = {history: {}, queue: {queue_pending: [[0, "p"]], queue_running: []}};
  const api = {
    addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(listener); },
    removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
    async queuePrompt() { posts++; return {prompt_id: "p"}; },
    async fetchApi(path) { requests.push(path); return {ok: true, json: async () => path === "/queue" ? data.queue : data.history}; },
  };
  const task = createInspectionTask({api, app: {graphToPrompt: async () => structuredClone(graph)}, node: {id: 6},
    reportKeys: ["report"], onReport: report => reports.push(report), onState: state => states.push(state),
    schedule: callback => {timers.set(++sequence, callback); return sequence;}, unschedule: id => timers.delete(id)});
  const event = (type, detail = {}) => { for (const cb of listeners.get(type) || []) cb({detail: {prompt_id: "p", ...detail}}); };
  const tick = async () => { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(cb => cb()); await flush(); };
  return {api, graph, data, task, states, reports, requests, event, tick, posts: () => posts,
    listenerCount: () => [...listeners.values()].reduce((n, s) => n + s.size, 0), timers};
}

test("silent upstream blocker finishes without report and cleans listeners/timer", async () => {
  const f = fixture(); await f.task.submit();
  assert.equal(f.states.at(-1).kind, "queued");
  f.event("execution_success"); await flush();
  assert.equal(f.states.at(-1).kind, "no_report"); assert.equal(f.task.busy, false);
  assert.equal(f.listenerCount(), 0); assert.equal(f.timers.size, 0); assert.equal(f.reports.length, 0);
});

test("events before POST response cannot be overwritten with queued", async () => {
  const f = fixture();
  f.api.queuePrompt = async () => {
    f.event("executed", {node: "6", output: {report: [JSON.stringify({state: "ready"})]}});
    f.event("execution_success"); return {prompt_id: "p"};
  };
  await f.task.submit(); await flush();
  assert.deepEqual(f.reports, [{state: "ready"}]); assert.equal(f.states.at(-1).kind, "reported");
});

test("other prompts and other nodes cannot satisfy the inspection", async () => {
  const f = fixture(); await f.task.submit();
  f.event("execution_error", {prompt_id: "other", exception_message: "wrong"});
  f.event("executed", {node: "99", output: {report: [{state: "ready"}]}});
  f.event("execution_success"); await flush();
  assert.equal(f.states.at(-1).kind, "no_report"); assert.equal(f.reports.length, 0);
});

test("cached report comes only from accepted prompt history", async () => {
  const f = fixture(); await f.task.submit();
  f.data.history = {p: {outputs: {"6": {report: [{state: "ready", cached: true}]}}, status: {status_str: "success"}}};
  f.event("execution_success"); await flush();
  assert.equal(f.reports[0].cached, true);
});

for (const type of ["execution_error", "execution_interrupted"]) test(`${type} clears pending`, async () => {
  const f = fixture(); await f.task.submit(); f.event(type, {exception_message: "upstream failed"}); await flush();
  assert.equal(f.states.at(-1).kind, type === "execution_error" ? "failed" : "interrupted");
  assert.equal(f.task.busy, false); assert.equal(f.listenerCount(), 0);
});

test("submission rejection and invalid response clear pending and permit retry", async () => {
  const f = fixture(); f.api.queuePrompt = async () => {throw Error("bad inputs");};
  await f.task.submit(); assert.equal(f.states.at(-1).kind, "submit_failed"); assert.equal(f.listenerCount(), 0);
  f.api.queuePrompt = async () => ({}); await f.task.submit();
  assert.equal(f.states.at(-1).kind, "submit_failed"); assert.equal(f.task.busy, false);
});

test("double click does not submit duplicate tasks", async () => {
  const f = fixture(); await Promise.all([f.task.submit(), f.task.submit()]); assert.equal(f.posts(), 1); f.task.dispose();
});

test("upstream changes during execution keep the old result stale", async () => {
  const f = fixture(); await f.task.submit(); f.graph.output["1"].inputs.file = "b.mp4";
  f.event("executed", {node: "6", output: {report: [{state: "ready"}]}});
  f.event("execution_success"); await flush();
  assert.equal(f.states.at(-1).kind, "stale"); assert.equal(f.reports.length, 0);
});

test("change then restore is still stale when local widget change was observed", async () => {
  const f = fixture(); await f.task.submit(); f.task.changed(); f.event("execution_success"); await flush();
  assert.equal(f.states.at(-1).kind, "stale");
});

test("history fallback recovers a missed terminal websocket message", async () => {
  const f = fixture(); await f.task.submit();
  f.data.history = {p: {outputs: {}, status: {status_str: "success", completed: true}}};
  await f.tick(); assert.equal(f.states.at(-1).kind, "no_report"); assert.equal(f.task.busy, false);
});

test("pending then running stays live, removed queue entry eventually becomes unknown", async () => {
  const f = fixture(); await f.task.submit(); await f.tick(); assert.equal(f.states.at(-1).kind, "queued");
  f.data.queue = {queue_pending: [], queue_running: [[0, "p"]]}; await f.tick();
  assert.equal(f.states.at(-1).kind, "running");
  f.data.queue = {queue_pending: [], queue_running: []}; await f.tick(); assert.equal(f.task.busy, true);
  await f.tick(); assert.equal(f.states.at(-1).kind, "unknown"); assert.equal(f.task.busy, false);
});

test("network failure never means success and never resubmits", async () => {
  const f = fixture(); await f.task.submit(); f.api.fetchApi = async () => {throw Error("offline");};
  await f.tick(); assert.equal(f.states.at(-1).kind, "unknown"); assert.equal(f.posts(), 1); assert.equal(f.task.busy, false);
});

test("removal while POST is pending cleans listeners and ignores late completion", async () => {
  const f = fixture(); let resolve;
  f.api.queuePrompt = () => new Promise(r => {resolve = r;});
  const pending = f.task.submit(); await flush(); f.task.dispose(); const count = f.states.length;
  resolve({prompt_id: "p"}); await pending;
  assert.equal(f.states.length, count); assert.equal(f.listenerCount(), 0); assert.equal(f.timers.size, 0);
});

test("lifecycle messages are localized and preserve raw error text", () => {
  setLocale("en");
  for (const kind of ["submitting", "queued", "running", "no_report", "failed", "interrupted", "submit_failed", "stale", "unknown"])
    assert.doesNotMatch(inspectionMessage({kind}), /[\u3400-\u9fff]/u);
  assert.match(inspectionMessage({kind: "failed", detail: "raw 0x123"}), /raw 0x123/);
  setLocale("zh"); assert.match(inspectionMessage({kind: "no_report"}), /上游重建开关/);
});

test("Comfy validation error preserves the failed node and actionable details only", () => {
  assert.equal(inspectionError({message: "Prompt execution failed", response: {
    error: {message: "Prompt outputs failed validation"}, node_errors: {
      "1": {errors: [{message: "Invalid file", details: "missing.mp4", extra_info: {unrelated: "not displayed"}}]},
    }}}), "Prompt outputs failed validation\n1: Invalid file missing.mp4");
  assert.equal(inspectionError(Error("offline")), "offline");
});
