import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { t, bindText, disposeTranslations, onLocaleChange } from "./i18n.js";
import { copyDiagnosticText } from "./preview_diagnostics.js";

const NODE = "DLSSExperimentalStorageManager";
const size = bytes => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GiB`
  : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(2)} MiB`
  : bytes >= 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${bytes} B`;
function el(tag, className = "", text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

app.registerExtension({
  name: "comfy-dlss-experimental.storage",
  nodeCreated(node) {
    if ((node.comfyClass || node.type) !== NODE || node.__dlssStorage) return;
    node.__dlssStorage = true;
    if (!document.querySelector("link[data-dlss-storage]")) {
      const link = el("link");
      link.rel = "stylesheet";
      link.dataset.dlssStorage = "1";
      link.href = new URL("./storage_manager.css", import.meta.url).href;
      document.head.append(link);
    }
    const root = el("section", "dlss-storage"), actions = el("div", "dlss-storage-actions");
    const status = el("p"), totals = el("pre"), list = el("div", "dlss-storage-list");
    const copyBox = el("textarea");
    copyBox.readOnly = true;
    copyBox.hidden = true;
    bindText(copyBox, () => t("任务详情（自动复制受限时，可全选后手动复制）"), "ariaLabel");
    const selected = new Set();
    let report = null, removed = false, pending = false;
    function button(label) {
      const b = el("button");
      b.type = "button";
      bindText(b, () => t(label));
      actions.append(b);
      return b;
    }
    const refresh = button("刷新文件列表"), copy = button("复制完整报告");
    const select = button("选中/取消本类前 200 项"), remove = button("删除所选文件");
    const apply = button("应用上方空间上限");
    const filter = el("select");
    filter.setAttribute("aria-label", "File category / 文件类别");
    for (const [value, label] of [["all", "全部文件类型"], ["prepared", "准备缓存"],
      ["temporary", "临时任务含预览"], ["execution", "执行记录"]]) {
      const option = el("option");
      option.value = value;
      bindText(option, () => t(label));
      filter.append(option);
    }
    const note = el("p");
    bindText(note, () => t("运行节点只刷新列表；上限须点击应用。临时任务包含预览视频，删除后旧预览和未保存的 VIDEO 会失效。"));
    root.append(actions, status, note, totals, filter, copyBox, list);
    for (const event of ["pointerdown", "wheel", "keydown", "keyup"]) {
      root.addEventListener(event, e => e.stopPropagation());
    }
    function updateButtons() {
      remove.disabled = pending || !selected.size || report?.busy;
      apply.disabled = pending || report?.busy;
      select.disabled = pending || report?.busy || !report?.entries?.length;
      refresh.disabled = pending;
      copy.disabled = !report;
    }
    function show(value) {
      if (removed || !value) return;
      report = value;
      selected.clear();
      status.textContent = report.busy ? t("渲染中，暂不可清理") : t("文件列表已刷新");
      const s = report.settings;
      totals.textContent = `${t("当前占用")}: ${size(report.bytes)} · ${report.entries.length} ${t("个条目")}\n` +
        `${t("生效上限")}: cache ${s.cache_gib} GiB · temp ${s.temporary_gib} GiB · job ${s.job_gib} GiB\n` +
        `entry ${s.entry_mib} MiB · free ${s.free_gib} GiB\n${report.roots.prepared}\n${report.roots.temporary}`;
      list.replaceChildren();
      for (const entry of report.entries) {
        if (filter.value !== "all" && entry.kind !== filter.value) continue;
        const row = el("div", "dlss-storage-entry"), label = el("label"), checkbox = el("input");
        checkbox.type = "checkbox";
        checkbox.disabled = !entry.removable;
        checkbox.dataset.entryId = entry.id;
        checkbox.addEventListener("change", () => {
          checkbox.checked ? selected.add(entry.id) : selected.delete(entry.id);
          updateButtons();
        });
        const category = entry.kind === "prepared" ? t("准备缓存") : entry.kind === "execution" ? t("执行记录") : t("临时任务含预览");
        label.append(checkbox, el("span", "", `${category} · ${size(entry.bytes)}`));
        const details = el("details"), title = el("summary", "", entry.name);
        details.append(title, el("pre", "", `${entry.path}\n${new Date(entry.modified * 1000).toLocaleString()}\n` +
          (entry.files || []).map(f => `${size(f.bytes)}  ${f.name}`).join("\n") + (entry.files_truncated ? "\n…" : "")));
        row.append(label, details);
        list.append(row);
      }
      updateButtons();
    }
    async function request(path, body) {
      const response = await api.fetchApi(`/dlss-experimental/storage${path}`, body === undefined ? {} : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || response.statusText);
      return value;
    }
    async function run(action) {
      pending = true;
      updateButtons();
      try { await action(); } catch (error) { status.textContent = error.message || String(error); }
      finally { pending = false; if (!removed) updateButtons(); }
    }
    refresh.onclick = () => run(async () => show(await request("")));
    filter.onchange = () => { if (report) show(report); };
    select.onclick = () => {
      const inputs = [...list.querySelectorAll("input[type=checkbox]:not(:disabled)")];
      const deselect = inputs.length && inputs.slice(0, 200).every(input => input.checked);
      selected.clear();
      for (const [index, input] of inputs.entries()) {
        input.checked = !deselect && index < 200;
        if (input.checked) selected.add(input.dataset.entryId);
      }
      updateButtons();
    };
    remove.onclick = () => {
      if (!selected.size || !window.confirm(t("永久删除所选缓存和临时任务？其中的预览及未保存视频将不可用；源视频和正式保存的输出不受影响。"))) return;
      const ids = [...selected];
      if (ids.length > 200) { status.textContent = t("每次最多删除 200 项，请分批选择。"); return; }
      run(async () => {
        const result = await request("/remove", { confirm: true, ids });
        show(await request(""));
        status.textContent = `${t("已释放空间")}: ${size(result.reclaimed_bytes)}`;
      });
    };
    apply.onclick = () => run(async () => {
      const settings = Object.fromEntries(["cache_gib", "temporary_gib", "job_gib", "entry_mib", "free_gib"].map(
        name => [name, node.widgets?.find(w => w.name === name)?.value]));
      await request("/settings", { confirm: true, settings });
      show(await request(""));
    });
    copy.onclick = async () => {
      copyBox.hidden = true;
      const result = await copyDiagnosticText(JSON.stringify(report, null, 2), { clipboard: navigator.clipboard, document, textbox: copyBox });
      status.textContent = result === "copied" ? t("已复制，可直接粘贴发送")
        : result === "manual" ? t("自动复制受限，详情已选中，请按 ⌘C / Ctrl+C") : t("复制失败");
    };
    node.addDOMWidget("dlss_storage", "DLSS_STORAGE", root, {
      serialize: false, hideOnZoom: false, getMinHeight: () => 250,
    });
    node.setSize([Math.max(node.size[0], 510), Math.max(node.size[1], 630)]);
    const executed = node.onExecuted;
    node.onExecuted = function(output, ...rest) {
      for (const r of output?.dlss_storage || []) show(r);
      return executed?.call(this, output, ...rest);
    };
    const unsubscribe = onLocaleChange(() => { if (report) show(report); });
    const onRemoved = node.onRemoved;
    node.onRemoved = function(...args) {
      removed = true;
      unsubscribe();
      disposeTranslations(root);
      return onRemoved?.apply(this, args);
    };
    updateButtons();
    // A new card reads live state once; no destructive action runs on load.
    run(async () => show(await request("")));
  },
});
