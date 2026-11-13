// Metrics explorer: series picked as scope (a node or a VM) × metric × aggregation over a time range, drawn on one
// chart. Pure helpers here (the catalogue of metrics, bucketing, statistics, the CSV); fetching lives in the page.

// unit: "pct" (0-100 scale), "mb" (sizes), "bps" (throughputs). A chart holds one unit: its axis has one scale.
export const METRICS = {
  cpu: { unit: "pct", pick: (r) => r.cpu_pct },
  mem_pct: { unit: "pct", pick: (r) => (r.mem_total_mb > 0 && r.mem_used_mb != null ? (r.mem_used_mb / r.mem_total_mb) * 100 : null) },
  mem_used: { unit: "mb", pick: (r) => r.mem_used_mb },
  disk_read: { unit: "bps", pick: (r) => r.disk_read_bps },
  disk_write: { unit: "bps", pick: (r) => r.disk_write_bps },
  net_rx: { unit: "bps", pick: (r) => r.net_rx_bps },
  net_tx: { unit: "bps", pick: (r) => r.net_tx_bps },
};
export const AGGREGATIONS = ["avg", "max", "min"];
export const RANGES = ["1h", "24h", "7j", "30j"];
// Buckets per range: about one point per minute over the hour, one per hour beyond (the stored hourly tier).
const BUCKET_MS = { "1h": 60_000, "24h": 3_600_000, "7j": 3_600_000 * 2, "30j": 3_600_000 * 8 };

export const seriesKey = (s) => `${s.scope.type}:${s.scope.id}|${s.metric}|${s.agg}`;
export const unitOf = (series) => (series.length ? METRICS[series[0].metric]?.unit : null);

const AGG = {
  avg: (v) => v.reduce((a, b) => a + b, 0) / v.length,
  max: (v) => Math.max(...v),
  min: (v) => Math.min(...v),
};

// Rows of a metrics history → [{ t, v }] of one metric, grouped into time buckets by the aggregation.
export function bucketize(rows, metric, agg, range) {
  const pick = METRICS[metric]?.pick;
  const size = BUCKET_MS[range] || 60_000;
  if (!pick) return [];
  const groups = new Map();
  for (const r of rows || []) {
    const t = Date.parse(r.ts);
    const v = pick(r);
    if (!Number.isFinite(t) || v == null || !Number.isFinite(v)) continue;
    const b = Math.floor(t / size) * size;
    if (!groups.has(b)) groups.set(b, []);
    groups.get(b).push(v);
  }
  return [...groups.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ t, v: AGG[agg](v) }));
}

export function stats(points) {
  if (!points?.length) return null;
  const v = points.map((p) => p.v);
  return { min: Math.min(...v), avg: AGG.avg(v), max: Math.max(...v), last: v[v.length - 1] };
}

// One row per time, one column per series (empty where a series has no point at that time).
export function toCsvRows(series, header) {
  const times = [...new Set(series.flatMap((s) => s.points.map((p) => p.t)))].sort((a, b) => a - b);
  const byKey = series.map((s) => new Map(s.points.map((p) => [p.t, p.v])));
  return [header, ...times.map((t) => [new Date(t).toISOString(), ...byKey.map((m) => (m.has(t) ? String(Math.round(m.get(t) * 100) / 100) : ""))])];
}

// Can `next` join a chart that already holds `series`? Not twice, not with another unit, not beyond six lines.
export const MAX_SERIES = 6;
export function canAdd(series, next) {
  if (series.length >= MAX_SERIES) return "max";
  if (series.some((s) => seriesKey(s) === seriesKey(next))) return "dup";
  const u = unitOf(series);
  if (u && METRICS[next.metric]?.unit !== u) return "unit";
  return null;
}
