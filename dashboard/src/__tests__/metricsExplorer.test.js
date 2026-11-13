import { describe, expect, it } from "vitest";
import { bucketize, canAdd, stats, toCsvRows } from "../next/lib/metricsExplorer";

const at = (min) => new Date(Date.UTC(2026, 10, 13, 10, min)).toISOString();
const rows = [
  { ts: at(0), cpu_pct: 10, mem_used_mb: 500, mem_total_mb: 1000 },
  { ts: at(0.5), cpu_pct: 30, mem_used_mb: 600, mem_total_mb: 1000 },
  { ts: at(1), cpu_pct: 50, mem_used_mb: null, mem_total_mb: 1000 },
];
const scope = { type: "node", id: "local" };

describe("metrics explorer", () => {
  it("groups samples into buckets with the chosen aggregation", () => {
    expect(bucketize(rows, "cpu", "avg", "1h").map((p) => p.v)).toEqual([20, 50]);
    expect(bucketize(rows, "cpu", "max", "1h").map((p) => p.v)).toEqual([30, 50]);
    expect(bucketize(rows, "mem_pct", "min", "1h").map((p) => p.v)).toEqual([50]); // the null sample is skipped
  });
  it("sums up a series", () => {
    expect(stats([{ v: 1 }, { v: 3 }, { v: 2 }])).toEqual({ min: 1, avg: 2, max: 3, last: 2 });
    expect(stats([])).toBeNull();
  });
  it("refuses a duplicate, another unit, or a seventh line", () => {
    const cpu = { scope, metric: "cpu", agg: "avg" };
    expect(canAdd([], cpu)).toBeNull();
    expect(canAdd([cpu], { ...cpu })).toBe("dup");
    expect(canAdd([cpu], { scope, metric: "mem_pct", agg: "avg" })).toBeNull(); // both percentages
    expect(canAdd([cpu], { scope, metric: "net_rx", agg: "avg" })).toBe("unit");
    expect(canAdd(Array.from({ length: 6 }, (_, i) => ({ scope: { type: "vm", id: `v${i}` }, metric: "cpu", agg: "avg" })), cpu)).toBe("max");
  });
  it("lays series side by side in a CSV", () => {
    const out = toCsvRows([{ points: [{ t: 0, v: 1.234 }] }, { points: [{ t: 60_000, v: 2 }] }], ["time", "a", "b"]);
    expect(out).toEqual([["time", "a", "b"], ["1970-01-01T00:00:00.000Z", "1.23", ""], ["1970-01-01T00:01:00.000Z", "", "2"]]);
  });
});
