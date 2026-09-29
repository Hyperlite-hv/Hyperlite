import { describe, expect, it } from "vitest";
import { bulkEligible, nameList, runBulk, splitSelection } from "../next/lib/bulk";
import { capabilities } from "../next/lib/capabilities";

const admin = capabilities("admin");
const viewer = capabilities("observateur");
const vm = (nom, etat, node = "local") => ({ nom, etat, node });

describe("bulk actions", () => {
  it("applies each action with the rules of the VM's own menu", () => {
    const sel = [vm("a", "actif"), vm("b", "arrete"), vm("c", "actif", "n2")];
    expect(splitSelection("start", sel, admin).apply.map((v) => v.nom)).toEqual(["b"]);
    expect(splitSelection("stop", sel, admin).apply.map((v) => v.nom)).toEqual(["a", "c"]);
    expect(splitSelection("delete", sel, admin)).toEqual({ apply: [sel[1]], skip: [sel[0], sel[2]] });
    expect(splitSelection("start", sel, viewer).apply).toEqual([]);
  });

  it("migrates only running VMs, and never to the node they already run on", () => {
    expect(bulkEligible("migrate", vm("a", "actif"), admin)).toBe(true);
    expect(bulkEligible("migrate", vm("a", "arrete"), admin)).toBe(false);
    expect(bulkEligible("migrate", vm("a", "actif", "n2"), admin, "n2")).toBe(false);
    expect(bulkEligible("migrate", vm("a", "actif"), viewer)).toBe(false);
  });

  it("runs every item, keeps going after a failure and never exceeds the limit", async () => {
    let running = 0;
    let peak = 0;
    const result = await runBulk([1, 2, 3, 4, 5, 6, 7], async (n) => {
      running += 1;
      peak = Math.max(peak, running);
      await new Promise((r) => setTimeout(r, 5));
      running -= 1;
      if (n % 3 === 0) throw new Error(`no ${n}`);
    }, 3);
    expect(peak).toBe(3);
    expect(result.ok.sort()).toEqual([1, 2, 4, 5, 7]);
    expect(result.failed.map((f) => [f.item, f.error.message]).sort()).toEqual([[3, "no 3"], [6, "no 6"]]);
    expect(await runBulk([], async () => {})).toEqual({ ok: [], failed: [] });
  });

  it("names a long selection without listing every VM", () => {
    const many = Array.from({ length: 11 }, (_, i) => vm(`vm${i}`, "actif"));
    expect(nameList(many.slice(0, 2), (n) => `+${n}`)).toBe("vm0, vm1");
    expect(nameList(many, (n) => `and ${n} more`, 3)).toBe("vm0, vm1, vm2 and 8 more");
  });
});

describe("tags typed by a user", async () => {
  const { parseTags, TAG_RE, allTags } = await import("../next/lib/meta");
  it("are split on commas and spaces, lowercased and deduplicated", () => {
    expect(parseTags(" Prod, db  prod,,client-x ")).toEqual(["prod", "db", "client-x"]);
    expect(parseTags("")).toEqual([]);
    expect(TAG_RE.test("rack-a")).toBe(true);
    expect(TAG_RE.test("-rack")).toBe(false);
  });
  it("are listed once per kind, sorted", () => {
    const byKey = { a: { kind: "vm", tags: ["web", "prod"] }, b: { kind: "vm", tags: ["prod"] }, c: { kind: "node", tags: ["rack"] } };
    expect(allTags(byKey, "vm")).toEqual(["prod", "web"]);
    expect(allTags(byKey)).toEqual(["prod", "rack", "web"]);
  });
});
