import { describe, expect, it, vi } from "vitest";
import { reloadForNewerServer } from "../api/client";

function memoryStorage() {
  const data = new Map();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, v) };
}

describe("a page older than the server's API", () => {
  it("reloads once, then not again within a minute", () => {
    const storage = memoryStorage();
    const reload = vi.fn();
    expect(reloadForNewerServer(1_000_000, storage, reload)).toBe(true);
    expect(reloadForNewerServer(1_030_000, storage, reload)).toBe(false);
    expect(reloadForNewerServer(1_061_000, storage, reload)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });
  it("never reloads without session storage", () => {
    const reload = vi.fn();
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => {} };
    expect(reloadForNewerServer(1, broken, reload)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});
