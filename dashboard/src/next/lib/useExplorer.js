import { useCallback, useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { fetchNodeMetricsHistory, fetchVMMetricsHistory } from "../../api/client";
import { bucketize, seriesKey } from "./metricsExplorer";

// The chart being explored and the charts pinned to Home, kept in this browser.
const CHART_KEY = "hl.explorer.chart";
const PINS_KEY = "hl.home.pins";
const read = (key, fallback) => { try { const v = JSON.parse(localStorage.getItem(key)); return v ?? fallback; } catch { return fallback; } };
const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* kept for this page only */ } };
const MAX_PINS = 8;

export const useExplorerStore = create((set, get) => ({
  chart: read(CHART_KEY, null), // { series: [{ scope: { type, id }, metric, agg }], range }
  pins: Array.isArray(read(PINS_KEY, [])) ? read(PINS_KEY, []) : [],
  setChart(chart) { write(CHART_KEY, chart); set({ chart }); },
  pin(chart, title) {
    const pins = [...get().pins, { id: `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`, title, ...chart }].slice(-MAX_PINS);
    write(PINS_KEY, pins); set({ pins });
  },
  unpin(id) { const pins = get().pins.filter((p) => p.id !== id); write(PINS_KEY, pins); set({ pins }); },
  move(id, delta) {
    const pins = [...get().pins];
    const i = pins.findIndex((p) => p.id === id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= pins.length) return;
    [pins[i], pins[j]] = [pins[j], pins[i]];
    write(PINS_KEY, pins); set({ pins });
  },
}));

// One read per scope and range, shared by every series and chart for a minute.
const CACHE_MS = 60_000;
const cache = new Map();
function history(scope, range) {
  const key = `${scope.type}:${scope.id}|${range}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.promise;
  const promise = (scope.type === "vm" ? fetchVMMetricsHistory(scope.name || scope.id.split("@")[0], range, scope.node) : fetchNodeMetricsHistory(scope.id, range))
    .then((r) => (Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : []));
  cache.set(key, { at: Date.now(), promise });
  promise.catch(() => cache.delete(key));
  return promise;
}

// The points of each series: { [seriesKey]: [{ t, v }] | "error" }, null while the first read runs.
export function useSeriesData(series, range) {
  const [data, setData] = useState(null);
  const seq = useRef(0);
  const sig = JSON.stringify([series.map(seriesKey), range]);
  const load = useCallback(async (force) => {
    const id = ++seq.current;
    if (force) cache.clear();
    const out = {};
    await Promise.all(series.map(async (s) => {
      try { out[seriesKey(s)] = bucketize(await history(s.scope, range), s.metric, s.agg, range); } catch { out[seriesKey(s)] = "error"; }
    }));
    if (id === seq.current) setData(out);
  }, [sig]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(false); }, [load]);
  return { data, reload: () => load(true) };
}
