import { describe, it, expect } from "vitest";
import { loadOrReload } from "../lib/lazyRetry";

function memoryStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}
const failing = async () => { throw new TypeError("Failed to fetch dynamically imported module"); };
const tick = () => new Promise((r) => setTimeout(r, 0));

describe("loading a code-split module", () => {
  it("returns the module when it loads, without reloading", async () => {
    let reloads = 0;
    await expect(loadOrReload(async () => ({ default: "ok" }), { storage: memoryStorage(), reload: () => { reloads += 1; } })).resolves.toEqual({ default: "ok" });
    expect(reloads).toBe(0);
  });

  it("reloads the page when the module cannot be fetched", async () => {
    let reloads = 0;
    void loadOrReload(failing, { storage: memoryStorage(), reload: () => { reloads += 1; }, now: () => 1000 });
    await tick();
    expect(reloads).toBe(1);
  });

  it("stops after three reloads in a minute and reports the error, then allows reloads again later", async () => {
    const storage = memoryStorage();
    let reloads = 0;
    const reload = () => { reloads += 1; };
    for (const t of [0, 10_000, 20_000]) { void loadOrReload(failing, { storage, reload, now: () => t }); await tick(); }
    expect(reloads).toBe(3);
    await expect(loadOrReload(failing, { storage, reload, now: () => 30_000 })).rejects.toThrow(/dynamically imported/);
    expect(reloads).toBe(3);
    void loadOrReload(failing, { storage, reload, now: () => 75_000 });
    await tick();
    expect(reloads).toBe(4);
  });
});
