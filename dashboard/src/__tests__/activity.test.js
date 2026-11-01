import { describe, expect, it } from "vitest";
import { accounts, byDay, durationText, foldRuns, remaining, runTargets } from "../next/lib/activity";

const NOW = new Date("2026-10-31T12:00:00Z").getTime();
const at = (minAgo) => new Date(NOW - minAgo * 60000).toISOString();
const task = (id, minAgo, extra = {}) => ({ id, type: "start_vm", username: "usine", status: "succeeded", started_at: at(minAgo), ...extra });

describe("the activity page's reading of the tasks", () => {
  it("folds one account's close tasks into a run, and leaves a pair alone", () => {
    const rows = [task("e", 1), task("d", 2), task("c", 3), task("b", 4), task("a", 30, { username: "admin" }), task("z", 31, { username: "admin" })];
    const items = foldRuns(rows);
    expect(items.map((i) => i.kind)).toEqual(["run", "task", "task"]);
    expect(items[0].tasks.map((t) => t.id)).toEqual(["e", "d", "c", "b"]);
    expect(items[0].username).toBe("usine");
  });

  it("splits a run where the account changes or the gap is long", () => {
    const rows = [task("c", 1), task("b", 2), task("a", 3), task("y", 20), task("x", 21), task("w", 22)];
    expect(foldRuns(rows).map((i) => i.kind)).toEqual(["run", "run"]);
  });

  it("a run is failed when one of its tasks failed, running while one runs", () => {
    const rows = [task("c", 1, { status: "failed" }), task("b", 2), task("a", 3)];
    expect(foldRuns(rows)[0].status).toBe("failed");
    expect(foldRuns([task("c", 1, { status: "running" }), ...rows.slice(1), task("z", 4)])[0].status).toBe("running");
  });

  it("names a run's targets, the most frequent first", () => {
    expect(runTargets([{ target: "t1" }, { target: "vm" }, { target: "vm" }, { target: null }])).toEqual(["vm", "t1"]);
  });

  it("puts the items into days with their counts", () => {
    const rows = [task("b", 5), task("a", 60 * 30, { status: "failed" })];
    const days = byDay(foldRuns(rows), NOW);
    expect(days.map((d) => d.day)).toEqual(["today", "yesterday"]);
    expect(days[1]).toMatchObject({ total: 1, failed: 1 });
  });

  it("estimates what is left from the median of past successes, only with enough of them", () => {
    const done = (id, s) => ({ id, type: "clone", status: "succeeded", started_at: at(100), finished_at: new Date(NOW - 100 * 60000 + s * 1000).toISOString() });
    const run = { id: "r", type: "clone", status: "running", started_at: at(0.5) };
    expect(remaining(run, [done("a", 60), done("b", 90)], NOW)).toBeNull();
    expect(Math.round(remaining(run, [done("a", 60), done("b", 90), done("c", 120)], NOW))).toBe(60);
    expect(remaining({ ...run, started_at: at(10) }, [done("a", 60), done("b", 90), done("c", 120)], NOW)).toBe(0);
  });

  it("writes durations for people", () => {
    expect(durationText(0.82, "fr")).toBe("0,8 s");
    expect(durationText(185, "fr")).toBe("3 min 05");
    expect(durationText(null, "fr")).toBe("—");
  });

  it("counts the tasks of each account, the busiest first", () => {
    expect(accounts([{ username: "usine" }, { username: "admin" }, { username: "usine" }, {}])).toEqual([
      { username: "usine", count: 2 }, { username: "admin", count: 1 }, { username: "system", count: 1 },
    ]);
  });
});
