export function formatUptime(seconds) {
  if (!seconds || seconds <= 0) return "--";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}j ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatMo(mo) {
  if (mo == null) return "--";
  if (mo >= 1024) return `${(mo / 1024).toFixed(1)} Go`;
  return `${Math.round(mo)} Mo`;
}

export function formatGo(go) {
  if (go == null) return "--";
  return `${go.toFixed(1)} Go`;
}

export function formatKbps(kbps) {
  if (kbps >= 1024) return `${(kbps / 1024).toFixed(1)} Mo/s`;
  return `${Math.round(kbps)} Ko/s`;
}
