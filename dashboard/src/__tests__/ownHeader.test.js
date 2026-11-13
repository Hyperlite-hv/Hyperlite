import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// The pages are loaded on demand, so the workspace cannot ask a page whether it draws its own header: the list lives in
// legacy/tabs.js. It must match the pages' own `X.ownHeader = true` markers, or a page gets two headers or none.
const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const tabs = readFileSync(here("../next/legacy/tabs.js"), "utf8");

describe("datacenter pages and their header", () => {
  it("OWN_HEADER lists exactly the pages that draw their own header", () => {
    const loaders = Object.fromEntries([...tabs.matchAll(/^const (\w+) = onDemand\(\(\) => import\("\.\.\/pages\/(\w+)"\)(?:, "(\w+)")?\);$/gm)].map(([, name, file, exp]) => [name, { file, exp: exp || "default" }]));
    const dc = tabs.match(/export const DATACENTER_TABS = \{([\s\S]*?)\};/)[1];
    const expected = [];
    for (const [, id, name] of dc.matchAll(/(\w+):\s*(\w+)/g)) {
      const { file, exp } = loaders[name];
      const src = readFileSync(here(`../next/pages/${file}.jsx`), "utf8");
      const comp = exp === "default" ? src.match(/export default function (\w+)/)?.[1] : exp;
      if (new RegExp(`^${comp}\\.ownHeader = true;`, "m").test(src)) expected.push(id);
    }
    const listed = JSON.parse(`[${tabs.match(/export const OWN_HEADER = new Set\(\[(.*)\]\);/)[1]}]`);
    expect([...listed].sort()).toEqual(expected.sort());
  });
});
