import { afterEach, describe, expect, it, vi } from "vitest";

function fakeStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), data };
}
async function load(initial) {
  vi.resetModules();
  const storage = fakeStorage(initial);
  vi.stubGlobal("localStorage", storage);
  return { ...(await import("../next/lib/vmViews")), storage };
}
afterEach(() => vi.unstubAllGlobals());

const vm = (nom, node = "local") => ({ nom, node });

describe("VM list views", () => {
  it("reads the old node toggle and ignores broken values", async () => {
    expect((await load({ "hyperlite-next-vmgroup": "0" })).readGroupMode()).toBe("none");
    expect((await load({ "hyperlite-next-vmgroup": "1" })).readGroupMode()).toBe("node");
    expect((await load({ "hyperlite-next-vmgroup": "pool" })).readGroupMode()).toBe("pool");
    expect((await load({ "hyperlite-next-vmgroup": "x" })).readGroupMode()).toBe("node");
    expect((await load({ "hyperlite-next-vmcols": "[\"uptime\",\"evil\",\"ip\"]" })).readColumns()).toEqual(["ip", "uptime"]);
    expect((await load({ "hyperlite-next-vmcols": "{" })).readColumns()).toEqual(["node", "ip", "res", "uptime"]);
  });

  it("saves, replaces and deletes named views, keeping only known fields", async () => {
    const { readSavedViews, saveView, deleteView, storage } = await load();
    let views = saveView([], " Prod ", { chip: "running", tagFilter: "prod", groupMode: "tag", columns: ["ip", "bad"], sort: { key: "name", dir: -1 }, extra: 1 });
    views = saveView(views, "Prod", { chip: "all" });
    views = saveView(views, "Lab", { groupMode: "nope" });
    expect(views.map((v) => v.name)).toEqual(["Lab", "Prod"]);
    expect(views[1].state).toEqual({ chip: "all" });
    expect(views[0].state).toEqual({});
    expect(readSavedViews()).toEqual(views);
    views = deleteView(views, "Lab");
    expect(JSON.parse(storage.data.get("hyperlite-next-vmviews")).map((v) => v.name)).toEqual(["Prod"]);
  });

  it("groups by tag and by pool, a VM under each of its groups, the others last", async () => {
    const { groupVms } = await load();
    const tags = { web: ["prod", "front"], db: ["prod"], lab: [] };
    const shown = [vm("web"), vm("db"), vm("lab"), vm("far", "n2")];
    const byTag = groupVms(shown, "tag", { tagsOf: (v) => tags[v.nom] || [], noneLabel: "No tag" });
    expect(byTag.map((g) => [g.label, g.vms.map((v) => v.nom)])).toEqual([["front", ["web"]], ["prod", ["web", "db"]], ["No tag", ["lab", "far"]]]);
    const pools = [{ name: "team", vms: ["db", "far"] }];
    const byPool = groupVms(shown, "pool", { pools, noneLabel: "No pool" });
    // Pools hold VMs of this node: the remote "far" of the same name is not a member.
    expect(byPool.map((g) => [g.label, g.vms.map((v) => v.nom)])).toEqual([["team", ["db"]], ["No pool", ["web", "lab", "far"]]]);
    const nodes = [{ id: "local", nom: "hv1" }, { id: "n2", nom: "hv2" }, { id: "n3", nom: "hv3" }];
    expect(groupVms(shown, "node", { nodes }).map((g) => g.label)).toEqual(["hv1", "hv2"]);
    expect(groupVms(shown, "node", { nodes, withEmpty: true }).map((g) => g.label)).toEqual(["hv1", "hv2", "hv3"]);
    expect(groupVms(shown, "none")).toBeNull();
  });
});
