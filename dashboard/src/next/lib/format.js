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
export function formatUptimeLong(seconds, lang = "en") {
  if (!seconds || seconds <= 0) return null;
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  const j = lang === "fr" ? "j" : "d";
  if (d > 0) return `${d} ${j} ${h} h`;
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
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
