import { describe, expect, it } from "vitest";
import { backupModeLabel, frequencyLabel, statusLabel } from "../lib/labels";

describe("wire value labels", () => {
  it("translates known wire values", () => {
    expect(statusLabel("running")).toBe("Running");
    expect(statusLabel("succeeded")).toBe("Completed");
    expect(frequencyLabel("weekly")).toBe("weekly");
    expect(backupModeLabel("live")).toBe("hot");
  });
  it("passes unknown values through unchanged", () => {
    expect(statusLabel("autre")).toBe("autre");
    expect(frequencyLabel(undefined)).toBeUndefined();
  });
});
