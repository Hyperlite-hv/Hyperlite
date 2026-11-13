import { describe, expect, it } from "vitest";
import { forResource, isHidden, recommendations } from "../next/lib/advisor";

const nodes = [
  { id: "local", name: "hv-01", state: "online", cpu_coeurs: 4, memoire_totale_mo: 1000, used_memory_mb: 950 },
  { id: "hv-02", name: "hv-02", state: "offline", cpu_coeurs: 4, memoire_totale_mo: 1000, used_memory_mb: 0 },
];
const vms = [
  { name: "web", node: "local", state: "active", vcpu: 12, guest_agent: "inactive" },
  { name: "db", node: "local", state: "active", vcpu: 8, guest_agent: "active" },
  { name: "old", node: "hv-02", state: "stopped", vcpu: 2 },
  { name: "tpl", node: "local", state: "stopped", template: true },
];
const ids = (list) => list.map((r) => r.id);

describe("advisor rules", () => {
  it("reports what the inventory shows, the most serious first", () => {
    const r = recommendations({ nodes, vms, pools: [{ name: "nas", node: "local", capacity_gb: 100, available_gb: 5 }, { name: "ok", node: "local", capacity_gb: 100, available_gb: 50 }] });
    expect(ids(r)).toEqual(["node-offline", "node-memory", "pool-full", "vm-no-agent", "node-vcpu", "vm-stopped"]);
    expect(r.find((x) => x.id === "node-vcpu").resources[0].value).toBe(5);
    expect(r.find((x) => x.id === "vm-stopped").resources.map((x) => x.id)).toEqual(["old@hv-02"]); // not the template
  });
  it("skips the rules whose source could not be read", () => {
    expect(ids(recommendations({ nodes, vms }))).not.toContain("vm-no-backup");
    const r = recommendations({ nodes, vms, scheduled: new Set(["web"]), users: [{ username: "root", role: "admin", totp_enabled: false }, { username: "ops", role: "operator" }], tokens: [{ id: 1, name: "ci", expires_at: null }, { id: 2, name: "x", expires_at: "2027-01-01" }], channels: 0 });
    expect(r.find((x) => x.id === "vm-no-backup").resources.map((x) => x.name)).toEqual(["db", "old"]);
    expect(r.find((x) => x.id === "admin-no-2fa").resources.map((x) => x.id)).toEqual(["root"]);
    expect(r.find((x) => x.id === "token-no-expiry").resources.map((x) => x.name)).toEqual(["ci"]);
    expect(ids(r)).toContain("no-channel");
  });
  it("advises a second node only to a one-node cluster that runs machines", () => {
    const one = [nodes[0]];
    expect(ids(recommendations({ nodes: one, vms }))).toContain("single-node");
    expect(ids(recommendations({ nodes: one, vms: [] }))).not.toContain("single-node");
  });
  it("finds the recommendations of one resource, and hides snoozed or dismissed ones", () => {
    const r = recommendations({ nodes, vms });
    expect(ids(forResource(r, "vm", "web"))).toEqual(["vm-no-agent"]);
    expect(isHidden({ a: "dismissed" }, "a")).toBe(true);
    expect(isHidden({ a: Date.now() + 1000 }, "a")).toBe(true);
    expect(isHidden({ a: Date.now() - 1000 }, "a")).toBe(false);
  });
});
