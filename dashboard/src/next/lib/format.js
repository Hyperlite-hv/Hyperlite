export function relativeTime(ts, lang = "en", now = Date.now()) {
  if (!ts) return "";
  const s = Math.max(0, Math.round((now - ts) / 1000));
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto", style: "short" });
  if (s < 5) return rtf.format(0, "second");
  if (s < 60) return rtf.format(-s, "second");
  if (s < 3600) return rtf.format(-Math.round(s / 60), "minute");
  return rtf.format(-Math.round(s / 3600), "hour");
}

export function formatDuration(startMs, endMs) {
  const end = endMs || Date.now();
  const s = Math.max(0, Math.round((end - startMs) / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function normalize(str) {
  return String(str ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

// Locale-aware sizes: 1024-based like the backend, French unit names (Mo/Go) in French.
export function formatSizeMb(mb, lang = "en") {
  if (mb == null || Number.isNaN(mb)) return null;
  const nf = new Intl.NumberFormat(lang, { maximumFractionDigits: 1 });
  const u = lang === "fr" ? { mb: "Mo", gb: "Go" } : { mb: "MB", gb: "GB" };
  return mb >= 1024 ? `${nf.format(mb / 1024)} ${u.gb}` : `${nf.format(Math.round(mb))} ${u.mb}`;
}
export function formatSizeGb(gb, lang = "en") {
  if (gb == null || Number.isNaN(gb)) return null;
  return `${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(gb)} ${lang === "fr" ? "Go" : "GB"}`;
}
// Throughput in bytes per second, 1024-based like the sizes (Ko/s in French). `compact` drops the decimals for axes.
export function formatRate(bps, lang = "en", compact = false) {
  if (bps == null || Number.isNaN(bps)) return null;
  const units = lang === "fr" ? ["o/s", "Ko/s", "Mo/s", "Go/s"] : ["B/s", "KB/s", "MB/s", "GB/s"];
  let v = Math.max(0, bps), i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  const nf = new Intl.NumberFormat(lang, { maximumFractionDigits: compact || i === 0 ? 0 : 1 });
  return `${nf.format(v)} ${units[i]}`;
}
export function formatUptimeLong(seconds, lang = "en") {
  if (!seconds || seconds <= 0) return null;
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  const j = lang === "fr" ? "j" : "d";
  if (d > 0) return `${d} ${j} ${h} h`;
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
}
// Date and time from an ISO string or a Unix timestamp in seconds (libvirt reports snapshot creation times that way).
export function formatDateTime(v, lang = "en") {
  if (v == null || v === "") return "";
  const d = typeof v === "number" || /^\d+$/.test(String(v)) ? new Date(Number(v) * 1000) : new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return new Intl.DateTimeFormat(lang, { dateStyle: "short", timeStyle: "short" }).format(d);
}
export function clockTime(iso, lang = "en") {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit" }).format(d);
}

// libvirt / QEMU report versions as major*1000000 + minor*1000 + release.
export function formatVersionInt(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return null;
  return `${Math.floor(n / 1000000)}.${Math.floor((n % 1000000) / 1000)}.${n % 1000}`;
}
