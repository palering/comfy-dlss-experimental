import { t } from "./i18n.js";
export const number = (v, unit = " MiB") => Number.isFinite(v) ? v.toFixed(2) + unit : t("不可用");
export const stages = () => ({ input_binding: t("输入检查"), input_preparation: t("颜色/光流准备"), runtime_snapshot: t("运行库校验"),
  color_conversion: t("↳ 像素转换"), optical_flow: t("↳ 光流计算"), cache_write: t("↳ 缓存写入"),
  cache_read_verify: t("逐帧缓存读取/校验"), worker_startup: t("进程启动/握手"),
  first_frame_and_warmup: t("等待首帧/初始化/预热"), nr_frames_and_transport: t("逐帧 NR + 传输"),
  worker_acquire: t("获取常驻实例（包含冷启动子项）"), worker_return: t("归还/释放实例"),
  history_reset_first_frame: t("复用实例：历史重置与首帧"),
  original_a_export: t("对照导出"), encoding_b: t("结果编码"), worker_finish: t("结束"), worker_cleanup: t("清理") });
const workerStates = () => ({ not_started: t("尚未启动"), starting: t("正在启动"), running: t("已启动"),
  releasing: t("正在释放"), released: t("已释放"), idle: t("已归还常驻实例（当时状态）"), cleanup_failed: t("释放失败，请查看记录") });

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
    t`任务 ${record.execution_id} · ${record.kind} · ${number(record.elapsed_seconds, " s")}`,
    `Worker ${workerStates()[record.worker_state] || t("状态未记录")}${record.release_requested ? t(" · 已请求终止并释放") : ""}`,
    t`执行阶段 ${stages()[record.stage] || record.stage}`,
    `Backend ${rt.backend || t("未知")} · ${rt.platform || t("未知")} · ${rt.execution_mode || rt.linux_display_backend || "native"}`,
    t`Proton ${rt.proton?.version || t("未使用")} · 实际策略：${rt.worker_policy === "persistent" ? t("按需启动并常驻") : t("每次任务后释放")}`,
    t`本次实例 ${record.worker_reused === true ? t("复用（不重复初始化预热）") : record.worker_reused === false ? t("新启动") : t("未记录")}`,
    ...(r ? [
      `${live ? t("最近采样") : t("历史采样（不是当前占用）")} ${new Date(r.sampled_at * 1000).toLocaleTimeString()}`,
      t`本任务进程组 RSS${rt.platform === "linux" ? t("（含 Proton）") : ""} ${number(r.worker?.rss_mib)}`,
      t`本任务 PID 匹配显存 ${number(r.worker_vram_mib)}（不是整卡显存）`,
      ...(r.worker?.processes || []).map(p => t`PID ${p.pid} ${p.name} · RSS ${number(p.rss_mib)} · 累计 CPU ${number(p.cpu_seconds, " s")}`),
    ] : [live ? t("正在等待资源采样；未采到不代表零占用。") : t("本任务没有可用资源采样。")]),
    ...Object.entries(rt.components || {}).map(([role, path]) => `${role}: ${path}\nSHA256 ${rt.component_hashes?.[role] || t("未知")}`),
    t("DLL 以内容哈希识别；尚未解析产品版本号。"),
  ];
}
export function historyLines(record) {
  return [
    ...workerLines(record), t`源文件 ${record.source_path || t("未解析")}`,
    t`输入缓存 ${record.guide_cache_hit === true ? t("复用") : record.guide_cache_hit === false ? t("新建") : t("未到达")}`,
    t`原始对照缓存 ${record.original_cache_hit === true ? t("复用") : record.original_cache_hit === false ? t("新建") : t("未使用")}`,
    ...Object.entries(record.stage_seconds || {}).map(([k, v]) => `${stages()[k] || k}: ${number(v, " s")}`),
    t`进程组 RSS 采样峰值 ${number(record.sampled_peak_rss_mib)}`,
    t`Worker 显存采样峰值 ${number(record.sampled_peak_worker_vram_mib)}`,
    t`参数 ${JSON.stringify(record.parameters, null, 2)}`,
    t`光流 ${JSON.stringify(record.flow_backend || record.guides, null, 2)}`,
    record.error ? t`错误 ${record.error}` : "",
    t`采样间隔 ${record.resource_sample_seconds ?? 2} 秒，可能漏掉峰值。RSS 可能重复统计共享页。`,
    t("输入准备包含其子项，不可重复相加。"),
    t("常驻模式 worker_acquire 包含 worker_startup；两项不能重复相加。"),
  ];
}

export function residentLines(worker) {
  const states = {idle: t("空闲常驻"), running: t("执行中"), releasing: t("正在释放"), cleanup_failed: t("释放失败")};
  const r = worker.resources, rt = worker.runtime || {};
  return [
    t`实例 ${worker.worker_id} · ${states[worker.state] || worker.state}${worker.release_pending ? t(" · 已请求释放") : ""}`,
    t`已接 ${worker.leases} 次任务 · 累计 ${worker.stream_frames} 帧 · ${worker.settings.width}×${worker.settings.height}`,
    worker.state === "idle" ? t`空闲剩余 ${Math.ceil(worker.idle_remaining_seconds)} 秒；到期自动释放` : t("新任务重置历史；更改 NR 参数/尺寸/运行库会重建实例"),
    ...(worker.state === "idle" && r ? [
      t`常驻资源采样 ${new Date(r.sampled_at * 1000).toLocaleTimeString()}`,
      t`进程组 RSS${rt.platform === "linux" ? t("（含 Proton）") : ""} ${number(r.worker?.rss_mib)} · PID 匹配显存 ${number(r.worker_vram_mib)}`,
      ...(r.worker?.processes || []).map(p => `PID ${p.pid} ${p.name} · RSS ${number(p.rss_mib)}`),
    ] : [worker.state === "idle" ? t("等待常驻资源采样（不是零占用）")
      : worker.state === "running" ? t("执行中资源见本次任务采样") : t("请等待资源释放；清理异常会在此处显示。")]),
    t`日志目录 ${worker.log_directory || t("未记录")}`,
    `Backend ${rt.backend || t("未知")} · ${rt.platform || t("未知")} · Proton ${rt.proton?.version || t("未使用")}`,
    ...Object.entries(rt.components || {}).map(([role, path]) => `${role}: ${path}\nSHA256 ${rt.component_hashes?.[role] || t("未知")}`),
    t("组件以 SHA256 标识版本，不是产品版本号。"),
    worker.error ? t`错误 ${worker.error}` : "",
  ];
}
