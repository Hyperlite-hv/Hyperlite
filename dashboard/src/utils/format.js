export function formatUptime(seconds) {
  if (!seconds || seconds <= 0) return "--";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatMo(mo) {
  if (mo == null) return "--";
  if (mo >= 1024) return `${(mo / 1024).toFixed(1)} GB`;
  return `${Math.round(mo)} MB`;
}

export function formatGo(go) {
  if (go == null) return "--";
  return `${go.toFixed(1)} GB`;
}

export function formatKbps(kbps) {
  if (kbps >= 1024) return `${(kbps / 1024).toFixed(1)} MB/s`;
  return `${Math.round(kbps)} KB/s`;
}
