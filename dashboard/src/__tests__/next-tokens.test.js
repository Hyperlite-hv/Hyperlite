import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

// Parses the design tokens from next.css and checks WCAG contrast (docs/frontend-rebuild/03 §2.4).
const css = readFileSync(new URL("../next/next.css", import.meta.url), "utf8");

function block(selector) {
  const i = css.indexOf(selector);
  const start = css.indexOf("{", i);
  return css.slice(start, css.indexOf("\n}", start));
}
function tokens(body) {
  const out = {};
  for (const m of body.matchAll(/--([\w-]+):\s*(#[0-9A-Fa-f]{6})\s*;/g)) out[m[1]] = m[2];
  return out;
}
const lum = (h) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

const dark = tokens(block(':root[data-ui="next"] {'));
const light = { ...dark, ...tokens(block(':root[data-ui="next"][data-theme="light"]')) };

for (const [name, T] of [["dark", dark], ["light", light]]) {
  describe(`${name} theme contrast`, () => {
    const surfaces = ["color-bg-workspace", "color-bg-surface", "color-bg-surface-raised", "color-bg-selected", "color-bg-hover"];
    it("text meets AA (4.5:1) on every surface, primary text 7:1", () => {
      for (const s of surfaces) {
        expect(ratio(T["color-text-primary"], T[s]), s).toBeGreaterThanOrEqual(7);
        expect(ratio(T["color-text-secondary"], T[s]), s).toBeGreaterThanOrEqual(4.5);
        expect(ratio(T["color-text-muted"], T[s]), s).toBeGreaterThanOrEqual(4.5);
      }
    });
    it("functional colours meet 4.5:1 as text on surfaces", () => {
      for (const c of ["accent", "info", "success", "warning", "danger", "offline", "unknown"]) {
        for (const s of surfaces) expect(ratio(T[`color-${c}`], T[s]), `${c} on ${s}`).toBeGreaterThanOrEqual(4.5);
      }
    });
    it("focus ring and control borders meet 3:1", () => {
      expect(ratio(T["color-border-focus"], T["color-bg-workspace"])).toBeGreaterThanOrEqual(3);
      expect(ratio(T["color-border-strong"], T["color-bg-surface"])).toBeGreaterThanOrEqual(3);
    });
    it("text on the accent button meets 4.5:1", () => {
      expect(ratio(T["color-text-on-accent"], T["color-accent"])).toBeGreaterThanOrEqual(4.5);
    });
    it("never uses pure black or white backgrounds in the dark theme", () => {
      if (name === "dark") for (const s of ["color-bg-app", "color-bg-sidebar", "color-bg-workspace", "color-bg-surface"]) expect(T[s].toLowerCase()).not.toBe("#000000");
    });
  });
}
