import { describe, expect, it } from "vitest";
import { daysUntil, linearTrend, memoryForecast, poolForecasts } from "../next/lib/forecast";

const DAY = 86_400_000;
const now = Date.UTC(2026, 10, 13);
const series = (days, start, perDay) => Array.from({ length: days + 1 }, (_, i) => ({ t: now - (days - i) * DAY, v: start + perDay * i }));

describe("capacity forecast", () => {
  it("finds the day a steady growth reaches the limit", () => {
    expect(daysUntil(series(10, 50, 2), 100, now)).toBeCloseTo(15, 5); // 70 today, +2 a day
  });
  it("gives no date for a flat or falling trend, nor for too little history", () => {
    expect(daysUntil(series(10, 50, 0), 100, now)).toBeNull();
    expect(daysUntil(series(10, 90, -1), 100, now)).toBeNull();
    expect(daysUntil(series(3, 50, 5), 100, now)).toBeNull(); // 4 points
    expect(linearTrend([{ t: now, v: 1 }, { t: now + 1000, v: 2 }, { t: now + 2000, v: 3 }, { t: now + 3000, v: 4 }, { t: now + 4000, v: 5 }, { t: now + 5000, v: 6 }])).toBeNull(); // span too short
  });
  it("says 0 when the limit is already reached", () => {
    expect(daysUntil(series(10, 95, 1), 100, now)).toBe(0);
  });
  it("lists the pools that fill within the horizon, the soonest first", () => {
    const pool = (name, start, perDay) => ({ node: "local", pool: name, points: series(14, start, perDay).map((x) => ({ ts: new Date(x.t).toISOString(), capacity_b: 1000, allocation_b: x.v })) });
    const out = poolForecasts([pool("slow", 100, 1), pool("fast", 500, 20), pool("flat", 500, 0)], { now });
    expect(out.map((x) => x.pool)).toEqual(["fast"]);
    expect(out[0].days).toBeCloseTo((1000 - 780) / 20, 5);
  });
  it("forecasts the memory of a host reaching 90 %", () => {
    const rows = series(10, 0.5, 0.02).map((x) => ({ ts: new Date(x.t).toISOString(), mem_total_mb: 1000, mem_used_mb: x.v * 1000 }));
    expect(memoryForecast(rows, { now })).toBeCloseTo(10, 5); // 70 % today, +2 points a day
  });
});
