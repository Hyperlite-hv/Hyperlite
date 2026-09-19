import { describe, expect, it } from "vitest";
import { NA, deriveFeatures, flattenCapabilities } from "../lib/capabilitiesView";

describe("capabilities view", () => {
  it("marks undetected fields instead of assuming them", () => {
    const rows = flattenCapabilities({});
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((r) => r.value !== undefined)).toBe(true);
    expect(rows.find((r) => r.key === "cpu.cores").value).toBe(NA);
  });
  it("reports ZFS as limited when the module is not loaded", () => {
    const features = deriveFeatures({ stockage: { zfs_disponible: false, zfs_installe: true }, securite: {} });
    const zfs = features.find((f) => f.id === "zfs");
    expect(zfs.etat).toBe("limite");
    expect(zfs.detail).toMatch(/Kernel module not loaded/);
  });
});
