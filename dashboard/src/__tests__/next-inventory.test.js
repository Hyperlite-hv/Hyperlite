import { describe, it, expect } from "vitest";
import { buildServerTree, buildPoolTree, filterTree, flatten, defaultOpen, highlight } from "../next/explorer/buildTree";
import { translate } from "../next/i18n";

const t = (k, v) => translate("en", k, v);
const nodes = [
  { id: "local", nom: "hl-devhub", etat: "online" },
  { id: "node-b", nom: "node-b", etat: "erreur", distant: true },
];
const vms = [
  { nom: "db-01", node: "local", etat: "actif", ip: "10.0.0.21", os: "Debian 12" },
  { nom: "win-2022", node: "local", etat: "plante", ip: null },
  { nom: "web-b1", node: "node-b", etat: "arrete", ip: null },
];
const data = { nodes, vms, containers: [{ nom: "ct1", etat: "actif", ip: "10.0.0.50" }], storagePools: [{ nom: "default", node: "local", type: "dir", etat: "actif", capacite_go: 100, disponible_go: 30 }], networks: [{ nom: "default", actif: true, pont: "virbr0", type: "nat" }] };
const isOpen = (row, level) => defaultOpen(row, level);

describe("Inventory Explorer tree", () => {
  it("Server mode: datacenter > nodes > categories > resources", () => {
    const [dc] = buildServerTree(data, t);
    expect(dc.kind).toBe("dc");
    expect(dc.children.map((n) => n.label)).toEqual(["hl-devhub", "node-b"]);
    const local = dc.children[0];
    expect(local.children.map((c) => c.kind)).toEqual(["category", "category", "category", "category"]);
    expect(local.children[0].children.map((v) => v.label)).toEqual(["db-01", "win-2022"]);
    // containers and networks are local-only data
    expect(dc.children[1].children.map((c) => c.key.split(":")[1])).toEqual(["vms", "storage"]);
  });

  it("bubbles the worst state of the children up to the parents", () => {
    const [dc] = buildServerTree(data, t);
    expect(dc.problems.danger).toBe(1);
    expect(dc.children[0].problems.danger).toBe(1);
    expect(dc.sev).toBeGreaterThanOrEqual(4);
  });

  it("keeps wire values as selection ids (VM selected by name, node by id)", () => {
    const [dc] = buildServerTree(data, t);
    expect(dc.children[0].selection).toEqual({ type: "node", id: "local" });
    expect(dc.children[0].children[0].children[0].selection).toEqual({ type: "vm", id: "db-01" });
  });

  it("Pool mode uses real membership and lists the rest under Unassigned", () => {
    const [dc] = buildPoolTree({ ...data, pools: [{ id: 1, name: "production", vms: ["db-01"] }] }, t);
    expect(dc.children.map((g) => g.label)).toEqual(["production", "Unassigned"]);
    expect(dc.children[0].children.map((v) => v.label)).toEqual(["db-01"]);
    // no duplicated VM rows across groups
    const all = dc.children.flatMap((g) => g.children.filter((c) => c.kind === "vm").map((c) => c.label));
    expect(new Set(all).size).toBe(all.length);
  });

  it("search matches name, IP, OS, state and node, keeps the parent path, counts results", () => {
    const [dc] = buildServerTree(data, t);
    for (const [q, expected] of [["db", 1], ["10.0.0.21", 1], ["debian", 1], ["crashed", 1], ["node-b", 1]]) {
      const { rows, matches } = filterTree([dc], q);
      expect(matches, q).toBeGreaterThanOrEqual(expected);
      expect(rows[0].kind).toBe("dc");
    }
    expect(filterTree([dc], "zzz").matches).toBe(0);
    expect(filterTree([dc], "zzz").rows).toHaveLength(0);
  });

  it("search is accent- and case-insensitive; empty query returns everything", () => {
    const [dc] = buildServerTree({ ...data, vms: [{ nom: "Été-01", node: "local", etat: "actif" }] }, t);
    expect(filterTree([dc], "ete").matches).toBe(1);
    expect(filterTree([dc], "").rows).toEqual([dc]);
  });

  it("flatten only lists rows of open branches with correct aria metadata", () => {
    const [dc] = buildServerTree(data, t);
    const flat = flatten([dc], isOpen);
    expect(flat[0]).toMatchObject({ level: 1, posinset: 1, setsize: 1 });
    const labels = flat.map((f) => f.row.label);
    expect(labels).toContain("db-01"); // VM categories are open by default
    expect(labels).not.toContain("default"); // storage / networks categories start closed
  });

  it("highlight splits the matching text", () => {
    expect(highlight("db-01", "db")).toEqual(["", { hit: "db" }, "-01"]);
    expect(highlight("db-01", "")).toEqual(["db-01"]);
  });
});

describe("scale: 1,000 VMs", () => {
  it("builds, filters and flattens the tree well under 100 ms (synthetic data, test only)", () => {
    const many = Array.from({ length: 1000 }, (_, i) => ({ nom: `vm-${String(i).padStart(4, "0")}`, node: i % 2 ? "local" : "node-b", etat: i % 50 === 0 ? "plante" : "actif", ip: `10.0.${Math.floor(i / 250)}.${i % 250}`, os: "Debian 12" }));
    const t0 = performance.now();
    const [dc] = buildServerTree({ ...data, vms: many }, t);
    const { rows, matches } = filterTree([dc], "vm-09");
    const flat = flatten([dc], isOpen);
    const elapsed = performance.now() - t0;
    expect(matches).toBe(100);
    expect(rows).toHaveLength(1);
    expect(flat.length).toBeGreaterThan(1000);
    expect(elapsed).toBeLessThan(100);
  });
});
