import { describe, expect, it } from "vitest";
import { listOf } from "./clusterList";

describe("listOf", () => {
  it("reads the cluster shape", () => {
    expect(listOf({ items: [{ id: 1, on: "node-a" }], unreachable: ["node-b"] })).toEqual([{ id: 1, on: "node-a" }]);
  });
  it("reads a plain list as it is, instead of showing nothing", () => {
    expect(listOf([{ id: 2 }])).toEqual([{ id: 2 }]);
  });
  it("gives an empty list for anything else", () => {
    expect(listOf(null)).toEqual([]);
    expect(listOf({ detail: "error" })).toEqual([]);
  });
});
