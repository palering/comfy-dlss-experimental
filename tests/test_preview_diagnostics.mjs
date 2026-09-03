import assert from "node:assert/strict";
import test from "node:test";
import { copyDiagnosticText, formatPreviewDiagnostics } from "../web/preview_diagnostics.js";

test("diagnostics snapshot includes effective execution data, not graph or media payloads", () => {
  const session = {
    session_id: "s1", state: "ready", selected_start: .833, source: { width: 280, height: 376 },
    frame_count: 1, profile_b: { nr_style: 2 }, process_to_end: true, input_duration: 36, range_duration: 36,
    execution: {
      execution_id: "e1", elapsed_seconds: 3.3825, parameters: { preview_scale: 50 },
      stage_seconds: { optical_flow: .047, input_preparation: .59 },
      guide_cache_hit: false, original_cache_hit: false,
      runtime: { component_hashes: { worker: "abc" } }, resources: { worker_vram_mib: 17 },
      error: null, result: { video: "not included" },
    },
    transport: { original_url: "not included" }, graph: "not included",
  };
  const before = structuredClone(session);
  const text = formatPreviewDiagnostics(session);
  assert.deepEqual(session, before);
  session.profile_b.nr_style = 4;
  const report = JSON.parse(text);
  assert.equal(report.preview.profile_b.nr_style, 2);
  assert.deepEqual(report.preview.source, { width: 280, height: 376 });
  assert.equal(report.preview.process_to_end, true);
  assert.equal(report.preview.input_duration, 36);
  assert.equal(report.preview.range_duration, 36);
  assert.equal(report.execution.elapsed_seconds, 3.3825);
  assert.equal(report.execution.guide_cache_hit, false);
  assert.equal(report.execution.runtime.component_hashes.worker, "abc");
  assert.equal(report.execution.result, undefined);
  assert.equal(report.preview.transport, undefined);
  assert.equal(report.preview.graph, undefined);
  assert.match(report.notes.join("\n"), /不能重复相加/);
});

test("blocked, failed, cancelled and incomplete sessions can be shared without timings", () => {
  for (const state of ["blocked", "failed", "cancelled", "preparing"]) {
    const report = JSON.parse(formatPreviewDiagnostics({ state, error: "示例错误", session_id: "s2" }));
    assert.equal(report.preview.state, state);
    assert.equal(report.preview.error, "示例错误");
    assert.equal(report.execution, null);
  }
  assert.throws(() => formatPreviewDiagnostics(null), /尚无/);
});

function fallbackFixture(command) {
  const calls = [];
  const textbox = {
    isConnected: true, hidden: true, value: "",
    focus() { calls.push("focus"); },
    select() { calls.push("select"); },
    setSelectionRange(start, end) { calls.push([start, end]); },
  };
  const document = {
    activeElement: { isConnected: true, focus() { calls.push("restore"); } },
    execCommand(name) { calls.push(name); return command(); },
  };
  return { calls, textbox, document };
}

test("async clipboard success does not focus, select or use the legacy command", async () => {
  const fixture = fallbackFixture(() => { throw new Error("must not run"); });
  let received;
  const result = await copyDiagnosticText("详情", {
    ...fixture, clipboard: { async writeText(value) { received = value; } },
  });
  assert.equal(result, "copied");
  assert.equal(received, "详情");
  assert.deepEqual(fixture.calls, []);
});

test("HTTP fallback executes synchronously within the gesture and cleans up on success", async () => {
  const fixture = fallbackFixture(() => true);
  const promise = copyDiagnosticText("报告", fixture);
  assert.deepEqual(fixture.calls, ["focus", "select", [0, 2], "copy", "restore"]);
  assert.equal(await promise, "copied");
  assert.equal(fixture.textbox.hidden, true);
  assert.equal(fixture.textbox.value, "");
});

test("async denial can still fall back to selected-text copy", async () => {
  const fixture = fallbackFixture(() => true);
  assert.equal(await copyDiagnosticText("report", {
    ...fixture, clipboard: { async writeText() { throw new Error("NotAllowedError"); } },
  }), "copied");
  assert.ok(fixture.calls.includes("copy"));
});

test("denied, throwing or unsupported legacy copy leaves a visible selected report", async () => {
  for (const mode of ["denied", "throws", "missing"]) {
    const fixture = fallbackFixture(() => {
      if (mode === "throws") throw new Error("NotAllowedError");
      return false;
    });
    if (mode === "missing") delete fixture.document.execCommand;
    const result = await copyDiagnosticText("详情\n多行", {
      ...fixture, clipboard: { async writeText() { throw new Error("NotAllowedError"); } },
    });
    assert.equal(result, "manual");
    assert.equal(fixture.textbox.hidden, false);
    assert.equal(fixture.textbox.value, "详情\n多行");
    assert.deepEqual(fixture.calls.slice(0, 3), ["focus", "select", [0, 5]]);
    assert.ok(!fixture.calls.includes("restore"));
  }
});

test("node removal during a clipboard prompt never steals focus back", async () => {
  const fixture = fallbackFixture(() => true);
  const result = await copyDiagnosticText("report", {
    ...fixture, clipboard: { async writeText() {
      fixture.textbox.isConnected = false;
      throw new Error("denied after node removal");
    } },
  });
  assert.equal(result, "unavailable");
  assert.deepEqual(fixture.calls, []);
});
