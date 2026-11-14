import { describe, expect, it } from "vitest";
import { DEFAULT_HOME, HOME_BLOCKS, moveBlock, normalizeHome } from "../next/lib/appearance";

describe("Home layout", () => {
  it("is made whole again from what was stored", () => {
    const h = normalizeHome({ main: ["resources", "bogus", "resources"], side: ["services"], hidden: ["watch", "nope"] });
    expect(h.main.slice(0, 1)).toEqual(["resources"]);
    expect(h.side[0]).toBe("services");
    expect([...h.main, ...h.side].sort()).toEqual([...HOME_BLOCKS].sort()); // every block once
    expect(h.hidden).toEqual(["watch"]);
    expect(normalizeHome(null)).toEqual(DEFAULT_HOME);
  });
  it("moves a block up, down and to the other column", () => {
    const h = normalizeHome(DEFAULT_HOME);
    expect(moveBlock(h, "services", -1).main.slice(0, 2)).toEqual(["services", "setup"]);
    expect(moveBlock(h, "setup", -1)).toBe(h); // already first
    const s = moveBlock(h, "watch", "switch");
    expect(s.side).not.toContain("watch");
    expect(s.main[s.main.length - 1]).toBe("watch");
  });
});
