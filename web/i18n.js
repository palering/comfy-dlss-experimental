import { english, chinese } from "./i18n_messages.js";

let locale = "en";
const listeners = new Set();
export function normalizeLocale(value) {
  return /^zh(?:[-_]|$)/i.test(String(value || "")) ? "zh" : "en";
}
export function getLocale() { return locale; }
export function setLocale(value) {
  const next = normalizeLocale(value);
  if (next === locale) return;
  locale = next;
  for (const listener of [...listeners]) listener();
}
export function onLocaleChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
// Both plain messages and tagged templates use literal placeholder substitution.
// No eval, HTML interpolation, regex replacement strings, or payload translation.
export function t(source, ...values) {
  const tagged = Array.isArray(source) && Object.hasOwn(source, "raw");
  const key = tagged ? source.reduce((text, part, index) => text + (index ? `{${index - 1}}` : "") + part, "") : String(source ?? "");
  const catalog = locale === "zh" ? chinese : english;
  const translated = Object.hasOwn(catalog, key) ? catalog[key] : key;
  return tagged ? translated.replace(/\{(\d+)\}/g, (match, index) => Number(index) < values.length ? String(values[Number(index)]) : match) : translated;
}

// Bind only explicit UI labels. Never scan/translate arbitrary DOM text or logs.
const bindings = new Map();
export function bindText(element, render, property = "textContent") {
  const update = () => { element[property] = render(); };
  update();
  const dispose = onLocaleChange(update);
  if (!bindings.has(element)) bindings.set(element, []);
  bindings.get(element).push(dispose);
}
export function disposeTranslations(root) {
  for (const [element, disposers] of bindings) {
    if (element === root || root.contains(element)) {
      for (const dispose of disposers) dispose();
      bindings.delete(element);
    }
  }
}
