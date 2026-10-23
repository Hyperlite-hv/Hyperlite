import { describe, expect, it } from "vitest";
import { trunksValid } from "../next/pages/VmConfigure";

describe("trunk VLANs: the same rule as the API (app/core/bridge_vlan.py)", () => {
  it("takes numbers and ranges from 1 to 4094, as Proxmox VE and vSphere write them", () => {
    for (const ok of ["10", "10,20-30", "10;20-30", " 10 , 20 ", "1-4094"]) expect(trunksValid(ok)).toBe(true);
  });
  it("refuses what the API would refuse", () => {
    for (const bad of ["", "0", "4095", "30-20", "a", "10,,20", "1-2-3", "10 20"]) expect(trunksValid(bad)).toBe(false);
  });
});
