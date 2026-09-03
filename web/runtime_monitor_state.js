export const number = (v, unit = " MiB") => Number.isFinite(v) ? v.toFixed(2) + unit : "不可用";
export const stages = { input_binding: "输入检查", input_preparation: "颜色/光流准备", runtime_snapshot: "运行库校验",
  color_conversion: "↳ 像素转换", optical_flow: "↳ 光流计算", cache_write: "↳ 缓存写入",
  cache_read_verify: "逐帧缓存读取/校验", worker_startup: "进程启动/握手",
  first_frame_and_warmup: "等待首帧/初始化/预热", nr_frames_and_transport: "逐帧 NR + 传输",
  worker_acquire: "获取常驻实例（包含冷启动子项）", worker_return: "归还/释放实例",
  history_reset_first_frame: "复用实例：历史重置与首帧",
  original_a_export: "对照导出", encoding_b: "结果编码", worker_finish: "结束", worker_cleanup: "清理" };
const workerStates = { not_started: "尚未启动", starting: "正在启动", running: "已启动",
  releasing: "正在释放", released: "已释放", idle: "已归还常驻实例（当时状态）", cleanup_failed: "释放失败，请查看记录" };

export function matchingRecords(records, preset) {
  return records.filter(record => !preset || record.runtime?.preset_path === preset);
}
export function canRelease(record) {
  return record.state === "running" && !record.release_requested && ["starting", "running"].includes(record.worker_state);
}
export function workerLines(record) {
  const r = record.resources, rt = record.runtime || {};
  const live = record.state === "running" && ["starting", "running", "releasing"].includes(record.worker_state);
  return [
    `任务 ${record.execution_id} · ${record.kind} · ${number(record.elapsed_seconds, " s")}`,
    `Worker ${workerStates[record.worker_state] || "状态未记录"}${record.release_requested ? " · 已请求终止并释放" : ""}`,
    `执行阶段 ${stages[record.stage] || record.stage}`,
    `Backend ${rt.backend || "未知"} · ${rt.platform || "未知"} · ${rt.execution_mode || rt.linux_display_backend || "native"}`,
    `Proton ${rt.proton?.version || "未使用"} · 实际策略：${rt.worker_policy === "persistent" ? "按需启动并常驻" : "每次任务后释放"}`,
    `本次实例 ${record.worker_reused === true ? "复用（不重复初始化预热）" : record.worker_reused === false ? "新启动" : "未记录"}`,
    ...(r ? [
      `${live ? "最近采样" : "历史采样（不是当前占用）"} ${new Date(r.sampled_at * 1000).toLocaleTimeString()}`,
      `本任务进程组 RSS${rt.platform === "linux" ? "（含 Proton）" : ""} ${number(r.worker?.rss_mib)}`,
      `本任务 PID 匹配显存 ${number(r.worker_vram_mib)}（不是整卡显存）`,
      ...(r.worker?.processes || []).map(p => `PID ${p.pid} ${p.name} · RSS ${number(p.rss_mib)} · 累计 CPU ${number(p.cpu_seconds, " s")}`),
    ] : [live ? "正在等待资源采样；未采到不代表零占用。" : "本任务没有可用资源采样。"]),
    ...Object.entries(rt.components || {}).map(([role, path]) => `${role}: ${path}\nSHA256 ${rt.component_hashes?.[role] || "未知"}`),
    "DLL 以内容哈希识别；尚未解析产品版本号。",
  ];
}
export function historyLines(record) {
  return [
    ...workerLines(record), `源文件 ${record.source_path || "未解析"}`,
    `输入缓存 ${record.guide_cache_hit === true ? "复用" : record.guide_cache_hit === false ? "新建" : "未到达"}`,
    `原始对照缓存 ${record.original_cache_hit === true ? "复用" : record.original_cache_hit === false ? "新建" : "未使用"}`,
    ...Object.entries(record.stage_seconds || {}).map(([k, v]) => `${stages[k] || k}: ${number(v, " s")}`),
    `进程组 RSS 采样峰值 ${number(record.sampled_peak_rss_mib)}`,
    `Worker 显存采样峰值 ${number(record.sampled_peak_worker_vram_mib)}`,
    `参数 ${JSON.stringify(record.parameters, null, 2)}`,
    `光流 ${JSON.stringify(record.flow_backend || record.guides, null, 2)}`,
    record.error ? `错误 ${record.error}` : "",
    `采样间隔 ${record.resource_sample_seconds ?? 2} 秒，可能漏掉峰值。RSS 可能重复统计共享页。`,
    "输入准备包含其子项，不可重复相加。",
    "常驻模式 worker_acquire 包含 worker_startup；两项不能重复相加。",
  ];
}

export function residentLines(worker) {
  const states = {idle: "空闲常驻", running: "执行中", releasing: "正在释放", cleanup_failed: "释放失败"};
  const r = worker.resources, rt = worker.runtime || {};
  return [
    `实例 ${worker.worker_id} · ${states[worker.state] || worker.state}${worker.release_pending ? " · 已请求释放" : ""}`,
    `已接 ${worker.leases} 次任务 · 累计 ${worker.stream_frames} 帧 · ${worker.settings.width}×${worker.settings.height}`,
    worker.state === "idle" ? `空闲剩余 ${Math.ceil(worker.idle_remaining_seconds)} 秒；到期自动释放` : "新任务重置历史；更改 NR 参数/尺寸/运行库会重建实例",
    ...(worker.state === "idle" && r ? [
      `常驻资源采样 ${new Date(r.sampled_at * 1000).toLocaleTimeString()}`,
      `进程组 RSS${rt.platform === "linux" ? "（含 Proton）" : ""} ${number(r.worker?.rss_mib)} · PID 匹配显存 ${number(r.worker_vram_mib)}`,
      ...(r.worker?.processes || []).map(p => `PID ${p.pid} ${p.name} · RSS ${number(p.rss_mib)}`),
    ] : [worker.state === "idle" ? "等待常驻资源采样（不是零占用）"
      : worker.state === "running" ? "执行中资源见本次任务采样" : "请等待资源释放；清理异常会在此处显示。"]),
    `日志目录 ${worker.log_directory || "未记录"}`,
    `Backend ${rt.backend || "未知"} · ${rt.platform || "未知"} · Proton ${rt.proton?.version || "未使用"}`,
    ...Object.entries(rt.components || {}).map(([role, path]) => `${role}: ${path}\nSHA256 ${rt.component_hashes?.[role] || "未知"}`),
    "组件以 SHA256 标识版本，不是产品版本号。",
    worker.error ? `错误 ${worker.error}` : "",
  ];
}
