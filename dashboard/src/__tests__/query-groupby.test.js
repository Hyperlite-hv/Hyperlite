import { describe, expect, it } from "vitest";
import { groupBy } from "../next/lib/queryObjects";

const o = (name, extra) => ({ name, kind: "vm", state: "running", nodeName: "hv-01", pools: [], tags: [], ...extra });

describe("saved query bars", () => {
  it("count by a field, largest first", () => {
    const found = [o("a"), o("b", { state: "stopped" }), o("c"), o("d", { nodeName: "hv-02" })];
    expect(groupBy(found, "state")).toEqual([{ key: "running", n: 3 }, { key: "stopped", n: 1 }]);
    expect(groupBy(found, "node")).toEqual([{ key: "hv-01", n: 3 }, { key: "hv-02", n: 1 }]);
  });
  it("an object with several tags counts in each, one without in —", () => {
    const found = [o("a", { tags: ["prod", "web"] }), o("b", { tags: ["prod"] }), o("c")];
    expect(groupBy(found, "tag")).toEqual([{ key: "prod", n: 2 }, { key: "—", n: 1 }, { key: "web", n: 1 }]);
  });
});
