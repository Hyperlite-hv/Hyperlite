import { useEffect, useState } from "react";
import { fetchNodeMetricsHistory, fetchStorageHistory } from "../../api/client";
import { linearTrend, memoryForecast, poolForecasts } from "./forecast";

// The month of history behind the capacity forecast, fetched once and kept a quarter of an hour: it moves by the hour.
const KEEP_MS = 15 * 60_000;
const cache = new Map(); // key → { at, value }

async function load(nodeIds) {
  const [storage, ...mems] = await Promise.all([
    fetchStorageHistory("30j").catch(() => []),
    ...nodeIds.map((id) => fetchNodeMetricsHistory(id, "30j").then((rows) => ({ id, rows })).catch(() => ({ id, rows: [] }))),
  ]);
  const history = Array.isArray(storage) ? storage : [];
  const pools = poolForecasts(history);
  const memory = mems.map(({ id, rows }) => ({ node: id, days: memoryForecast(rows) })).filter((m) => m.days != null).sort((a, b) => a.days - b.days);
  // Whether any series is long enough to say something, so "nothing fills up" is not claimed from two samples.
  const enough = history.some((s) => linearTrend((s.points || []).map((x) => ({ t: Date.parse(x.ts), v: x.allocation_b }))))
    || mems.some(({ rows }) => linearTrend((Array.isArray(rows) ? rows : []).map((r) => ({ t: Date.parse(r.ts), v: r.mem_used_mb }))));
  return { pools, memory, enough };
}

function cached(key) {
  const hit = cache.get(key);
  return hit && Date.now() - hit.at < KEEP_MS ? hit.value : null;
}

// { pools: [{ node, pool, days }], memory: [{ node, days }], enough } within 90 days, or null while loading.
export function useCapacityForecast(nodeIds) {
  const key = [...nodeIds].sort().join("|");
  const [value, setValue] = useState(() => cached(key));
  useEffect(() => {
    const hit = cached(key);
    if (hit) { setValue(hit); return undefined; }
    let alive = true;
    load(key ? key.split("|") : []).then((v) => { cache.set(key, { at: Date.now(), value: v }); if (alive) setValue(v); });
    return () => { alive = false; };
  }, [key]);
  return value;
}

export function resetForecastCache() { cache.clear(); }
