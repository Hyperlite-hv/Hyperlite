import { describe, expect, it } from "vitest";
import { backupModeLabel, frequencyLabel, statusLabel } from "../lib/labels";

describe("wire value labels", () => {
  it("translates known wire values", () => {
    expect(statusLabel("en_cours")).toBe("Running");
    expect(statusLabel("termine")).toBe("Completed");
    expect(frequencyLabel("hebdomadaire")).toBe("weekly");
    expect(backupModeLabel("chaud")).toBe("hot");
  });
  it("passes unknown values through unchanged", () => {
    expect(statusLabel("autre")).toBe("autre");
    expect(frequencyLabel(undefined)).toBeUndefined();
  });
});
