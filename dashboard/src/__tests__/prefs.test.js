import { afterEach, describe, expect, it, vi } from "vitest";

function fakeStorage(initial) {
  const data = new Map(Object.entries(initial));
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), data };
}

async function loadWith(stored) {
  vi.resetModules();
  const storage = fakeStorage(stored === undefined ? {} : { "hyperlite-next-prefs": stored });
  vi.stubGlobal("localStorage", storage);
  return { ...(await import("../next/lib/prefs")), storage };
}

afterEach(() => vi.unstubAllGlobals());

describe("personal preferences", () => {
  it("falls back to the defaults for missing, broken or out-of-range values", async () => {
    for (const stored of [undefined, "not json", JSON.stringify({ termFont: "comic", termFontSize: 99, overviewPools: "all" })]) {
      const { usePrefs, termOptions } = await loadWith(stored);
      expect(usePrefs.getState()).toMatchObject({ termFont: "plex", termFontSize: 13, overviewPools: null });
      expect(termOptions()).toMatchObject({ fontSize: 13, fontFamily: "IBM Plex Mono, ui-monospace, monospace" });
    }
  });

  it("keeps valid choices and saves each change", async () => {
    const { usePrefs, termOptions, storage } = await loadWith(JSON.stringify({ termFont: "dejavu", termFontSize: 16, overviewPools: ["local:default", 3] }));
    expect(usePrefs.getState()).toMatchObject({ termFont: "dejavu", termFontSize: 16, overviewPools: ["local:default"] });
    usePrefs.getState().update({ termFontSize: 20 });
    expect(termOptions().fontSize).toBe(20);
    expect(JSON.parse(storage.data.get("hyperlite-next-prefs"))).toEqual({ termFont: "dejavu", termFontSize: 20, overviewPools: ["local:default"] });
    usePrefs.getState().reset();
    expect(JSON.parse(storage.data.get("hyperlite-next-prefs"))).toEqual({ termFont: "plex", termFontSize: 13, overviewPools: null });
  });
});
