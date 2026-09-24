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
