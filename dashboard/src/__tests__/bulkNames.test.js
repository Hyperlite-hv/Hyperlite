import { describe, expect, it } from "vitest";
import { bulkNames, bulkProblems } from "../next/lib/bulkNames";

describe("names of several copies", () => {
  it("numbers them from the start, with the digits the last one needs", () => {
    expect(bulkNames("web", 1, 3)).toEqual(["web-01", "web-02", "web-03"]);
    expect(bulkNames("web", 98, 3)).toEqual(["web-098", "web-099", "web-100"]);
  });
  it("says which names cannot be used", () => {
    expect(bulkProblems(["web-01", "web-02", "-x-03"], new Set(["web-02"]))).toEqual({ "web-02": "taken", "-x-03": "rule" });
  });
});
