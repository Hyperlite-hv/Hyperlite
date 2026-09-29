import { describe, expect, it } from "vitest";
import { findVm, parseVmKey, vmKey } from "../next/lib/vmId";

const vms = [
  { nom: "test", node: "local" },
  { nom: "test", node: "node-b" },
  { nom: "only-remote", node: "node-b" },
];

describe("VM identity (node, name)", () => {
  it("keeps the bare name for a local VM and adds the node otherwise", () => {
    expect(vmKey(vms[0])).toBe("test");
    expect(vmKey(vms[1])).toBe("test@node-b");
    expect(parseVmKey("test@node-b")).toEqual({ nom: "test", node: "node-b" });
    expect(parseVmKey("test")).toEqual({ nom: "test", node: "local" });
  });
  it("tells two homonymous VMs apart", () => {
    expect(findVm(vms, "test")).toBe(vms[0]);
    expect(findVm(vms, "test@node-b")).toBe(vms[1]);
    expect(findVm(vms, "test@node-c")).toBeUndefined();
  });
  it("still resolves a name-only reference to a VM that exists on one node only", () => {
    expect(findVm(vms, "only-remote")).toBe(vms[2]);
  });
});
