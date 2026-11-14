import { describe, expect, it } from "vitest";
import { cachedValue, clearCached, remember } from "../next/lib/useCached";

describe("data cached between visits", () => {
  it("gives back the last read of a key, nothing for another", () => {
    clearCached();
    expect(cachedValue("rules")).toBeNull();
    remember("rules", [1, 2]);
    expect(cachedValue("rules")).toEqual([1, 2]);
    expect(cachedValue("other")).toBeNull();
    clearCached();
    expect(cachedValue("rules")).toBeNull();
  });
});
