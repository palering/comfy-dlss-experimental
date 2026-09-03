import assert from "node:assert/strict";
import test from "node:test";
import { canRelease, historyLines, matchingRecords, number, workerLines, residentLines } from "../web/runtime_monitor_state.js";
import { setLocale } from '../web/i18n.js';
setLocale('zh');

test("manual release is available only for a live, unrequested worker", () => {
  for (const worker_state of ["not_started", "released", "cleanup_failed", "releasing", undefined]) {
    assert.equal(canRelease({state: "running", worker_state}), false);
  }
  for (const worker_state of ["starting", "running"]) {
    assert.equal(canRelease({state: "running", worker_state}), true);
    assert.equal(canRelease({state: "success", worker_state}), false);
    assert.equal(canRelease({state: "running", worker_state, release_requested: true}), false);
  }
});
test("preset filtering never exposes another preset's release target", () => {
  const records = [{runtime: {preset_path: "/a"}}, {runtime: {preset_path: "/b"}}, {}];
  assert.deepEqual(matchingRecords(records, "/a"), [records[0]]);
});
test("historical resources cannot masquerade as the current worker or whole GPU", () => {
  const record = {state: "success", worker_state: "released", runtime: {platform: "linux"}, resources: {
    sampled_at: 0, worker: {rss_mib: 1312, processes: []}, worker_vram_mib: 699,
    gpu: {gpus: [{memory_used_mib: 774}]},
  }};
  const text = workerLines(record).join("\n");
  assert.match(text, /历史采样（不是当前占用）/);
  assert.match(text, /699.00 MiB（不是整卡显存）/);
  assert.doesNotMatch(text, /774/);
  assert.match(text, /含 Proton/);
  assert.doesNotMatch(workerLines({...record, runtime: {platform: "windows"}}).join("\n"), /含 Proton/);
  assert.match(workerLines({...record, state: "running", worker_state: "running"}).join("\n"), /最近采样/);
});
test("missing samples are unknown and old history retains the old sampling interval", () => {
  assert.equal(number(null), "不可用");
  assert.equal(number(0), "0.00 MiB");
  assert.match(workerLines({state: "running", worker_state: "running"}).join("\n"), /未采到不代表零/);
  assert.match(historyLines({}).join("\n"), /采样间隔 2 秒/);
  assert.match(historyLines({resource_sample_seconds: 1}).join("\n"), /采样间隔 1 秒/);
});

test("idle resident identifies its runtime and owned resources separately from old tasks", () => {
  const worker = {worker_id: "own-id", state: "idle", leases: 2, stream_frames: 26,
    settings: {width: 280, height: 376}, idle_remaining_seconds: 299.2,
    runtime: {backend: "direct_nr", platform: "linux", components: {nvngx_dlssnr: "/runtime/nr.dll"},
      component_hashes: {nvngx_dlssnr: "exact-hash"}, proton: {version: "test-proton"}},
    resources: {sampled_at: 0, worker: {rss_mib: 1200, processes: [{pid: 123, name: "worker", rss_mib: 800}]},
      worker_vram_mib: 699, gpu: {gpus: [{memory_used_mib: 999}]}}};
  const text = residentLines(worker).join("\n");
  assert.match(text, /空闲常驻/);
  assert.match(text, /空闲剩余 300 秒/);
  assert.match(text, /PID 匹配显存 699.00 MiB/);
  assert.doesNotMatch(text, /999/);
  assert.match(text, /SHA256 exact-hash/);
  assert.match(text, /Proton test-proton/);
  assert.doesNotMatch(residentLines({...worker, state: "running"}).join("\n"), /699|常驻资源采样/);
  assert.match(residentLines({...worker, resources: null}).join("\n"), /不是零占用/);
});
