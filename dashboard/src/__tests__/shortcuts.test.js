import { describe, expect, it } from "vitest";
import { GO, isTyping } from "../next/lib/shortcuts";
import { SERVICE } from "../next/lib/services";

describe("keyboard shortcuts", () => {
  it("go to services that exist, each letter once", () => {
    for (const id of Object.values(GO)) expect(SERVICE[id], id).toBeTruthy();
    expect(new Set(Object.keys(GO)).size).toBe(Object.keys(GO).length);
  });
  it("leave the keys alone while a field is being typed in", () => {
    expect(isTyping({ tagName: "INPUT" })).toBe(true);
    expect(isTyping({ tagName: "TEXTAREA" })).toBe(true);
    expect(isTyping({ tagName: "DIV", isContentEditable: true })).toBe(true);
    expect(isTyping({ tagName: "BUTTON" })).toBe(false);
  });
});
