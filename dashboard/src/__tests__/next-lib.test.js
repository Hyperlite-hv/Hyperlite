import { describe, it, expect } from "vitest";
import { normalizeDetail } from "../next/lib/errors";
import { deriveAlerts, summarizeHealth } from "../next/lib/alerts";
import { stateInfo } from "../next/lib/enums";
import { capabilities, vmActionState } from "../next/lib/capabilities";
import { formatVersionInt } from "../next/lib/format";
import { resolveTheme } from "../next/tokens/theme";
import { selectionToPath, withTab } from "../next/lib/urls";
import en from "../next/i18n/en";
import fr from "../next/i18n/fr";

describe("error normalisation", () => {
  it("handles strings, string lists and pydantic 422 objects (never [object Object])", () => {
    expect(normalizeDetail("boom")).toBe("boom");
    expect(normalizeDetail(["a", "b"])).toBe("a ; b");
    expect(normalizeDetail([{ loc: ["body", "vcpu"], msg: "must be >= 1", type: "x" }])).toBe("vcpu: must be >= 1");
    expect(normalizeDetail(null)).toBe("");
  });
});

describe("derived alerts", () => {
  it("flags crashed VMs, offline nodes, full pools and failed tasks; sorted by severity", () => {
    const alerts = deriveAlerts({
      nodes: [{ id: "n", nom: "n", etat: "erreur" }],
      vms: [{ nom: "a", node: "local", etat: "plante" }, { nom: "b", node: "local", etat: "bloque" }, { nom: "c", node: "local", etat: "actif" }],
      storagePools: [{ nom: "tank", node: "local", etat: "actif", capacite_go: 100, disponible_go: 5 }, { nom: "ok", node: "local", etat: "actif", capacite_go: 100, disponible_go: 90 }],
      tasks: [{ id: 1, statut: "echec", type: "backup_vm", cible: "a", erreur: "no space" }],
    });
    expect(alerts.map((a) => a.kind)).toEqual(["vm-crashed", "pool-full", "task-failed", "vm-blocked", "node-offline"]);
  });
  it("is empty when everything is healthy", () => {
    expect(deriveAlerts({ nodes: [{ id: "l", nom: "l", etat: "online" }], vms: [{ nom: "a", node: "l", etat: "actif" }] })).toEqual([]);
  });
});

describe("states, capabilities, urls, theme, i18n", () => {
  it("maps every wire state to shape + tone, unknown values fall back", () => {
    expect(stateInfo("vm", "plante")).toMatchObject({ shape: "diamond", tone: "danger" });
    expect(stateInfo("vm", "n-importe-quoi").tone).toBe("unknown");
    expect(stateInfo("node", "erreur").tone).toBe("offline");
  });
  it("only admins get power actions, with a reason otherwise; never widens a right", () => {
    const obs = capabilities("observateur");
    expect(vmActionState("start", { etat: "arrete" }, obs)).toEqual({ enabled: false, reason: "menu.reason.admin" });
    const adm = capabilities("admin");
    expect(vmActionState("start", { etat: "actif" }, adm).enabled).toBe(false);
    expect(vmActionState("stop", { etat: "actif" }, adm).enabled).toBe(true);
    expect(vmActionState("delete", { etat: "actif" }, adm).reason).toBe("menu.reason.mustStop");
  });
  it("keeps the historical URLs", () => {
    expect(selectionToPath({ type: "datacenter" })).toBe("/datacenter");
    expect(selectionToPath({ type: "vm", id: "web 01" })).toBe("/vm/web%2001");
    expect(withTab("/node/local", "disk")).toBe("/node/local?tab=disk");
    expect(withTab("/node/local", "summary")).toBe("/node/local");
  });
  it("resolves the system theme", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
  });
  it("English and French catalogues have exactly the same keys and no empty text", () => {
    expect(Object.keys(fr).sort()).toEqual(Object.keys(en).sort());
    for (const k of Object.keys(en)) { expect(en[k], k).not.toBe(""); expect(fr[k], k).not.toBe(""); }
  });
  it("French placeholders match English ones", () => {
    for (const k of Object.keys(en)) {
      const ph = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
      expect(ph(fr[k]), k).toBe(ph(en[k]));
    }
  });
});

describe("global health badge", () => {
  it("is ok without alerts, attention for warnings/offline nodes, critical when something is red", () => {
    expect(summarizeHealth([]).level).toBe("ok");
    expect(summarizeHealth([{ level: "warning" }, { level: "offline" }])).toMatchObject({ level: "attention", attention: 2 });
    expect(summarizeHealth([{ level: "danger" }, { level: "warning" }])).toMatchObject({ level: "critical", critical: 1, attention: 1 });
  });
});

describe("version formatting", () => {
  it("decodes libvirt/QEMU integer versions", () => {
    expect(formatVersionInt(9000000)).toBe("9.0.0");
    expect(formatVersionInt(7002022)).toBe("7.2.22");
    expect(formatVersionInt(null)).toBeNull();
  });
});
