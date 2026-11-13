// When a growing quantity reaches a limit, from its recent history: a least-squares line through the samples.
// Kept deliberately simple and cautious: too few samples, too short a span or a flat or falling trend give no date,
// rather than a confident guess.
const DAY = 86_400_000;

// points: [{ t: epoch ms, v: number }]. Returns { slope (per day), at(t) } or null when the history says too little.
export function linearTrend(points, { minPoints = 6, minSpanDays = 2 } = {}) {
  const p = (points || []).filter((x) => Number.isFinite(x?.t) && Number.isFinite(x?.v));
  if (p.length < minPoints) return null;
  const t0 = p[0].t;
  const span = (p[p.length - 1].t - t0) / DAY;
  if (span < minSpanDays) return null;
  const xs = p.map((x) => (x.t - t0) / DAY);
  const n = p.length;
  const mx = xs.reduce((a, x) => a + x, 0) / n;
  const my = p.reduce((a, x) => a + x.v, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i++) { num += (xs[i] - mx) * (p[i].v - my); den += (xs[i] - mx) ** 2; }
  if (den === 0) return null;
  const slope = num / den;
  const intercept = my - slope * mx;
  return { slope, at: (t) => intercept + slope * ((t - t0) / DAY) };
}

// Days from `now` until the trend reaches `limit`: 0 when it is already there, null when it does not grow.
export function daysUntil(points, limit, now = Date.now(), opts) {
  const trend = linearTrend(points, opts);
  if (!trend || !(limit > 0)) return null;
  const current = trend.at(now);
  if (current >= limit) return 0;
  if (trend.slope <= 0) return null;
  return (limit - current) / trend.slope;
}

// The pools of a storage history (GET /storage/history) that fill up within `horizonDays`, the soonest first.
export function poolForecasts(history, { now = Date.now(), horizonDays = 90 } = {}) {
  const out = [];
  for (const s of history || []) {
    const pts = (s.points || []).filter((x) => x.capacity_b > 0).map((x) => ({ t: Date.parse(x.ts), v: x.allocation_b }));
    const cap = (s.points || []).reduce((m, x) => (x.capacity_b > 0 ? x.capacity_b : m), 0);
    const days = daysUntil(pts, cap, now);
    if (days != null && days <= horizonDays) out.push({ node: s.node, pool: s.pool, days });
  }
  return out.sort((a, b) => a.days - b.days);
}

// The day the memory of a host reaches `ratio` of its total (GET /nodes/{id}/metrics/history rows).
export function memoryForecast(rows, { now = Date.now(), ratio = 0.9, horizonDays = 90 } = {}) {
  const pts = (rows || []).filter((r) => r.mem_total_mb > 0 && r.mem_used_mb != null).map((r) => ({ t: Date.parse(r.ts), v: r.mem_used_mb / r.mem_total_mb }));
  const days = daysUntil(pts, ratio, now);
  return days != null && days <= horizonDays ? days : null;
}

// "Full now", "Full in 12 days" for a pool; "Reached", "In 12 days" for a threshold; "Now", "12 days" under a "Full in" heading (rounded down: the cautious side).
export function formatDays(days, t, kind = "full") {
  const d = Math.max(0, Math.floor(days));
  const k = kind === "full" ? "fc" : kind === "short" ? "fc.short" : "fc.in";
  return d === 0 ? t(`${k}.today`) : t(d === 1 ? `${k}.days.one` : `${k}.days`, { n: d });
}
