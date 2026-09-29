import { describe, expect, it } from "vitest";
import { KEY_COMBOS, MAX_TYPED, sendCombo, textToKeysyms } from "../next/lib/typeText";

// The Latin-1 rule of noVNC's keysymdef.lookup, enough to check the mapping.
const lookup = (u) => (u >= 0x20 && u <= 0xff ? u : 0x01000000 | u);

describe("typing into the console", () => {
  it("maps characters, Enter and Tab, and drops carriage returns", () => {
    expect(textToKeysyms("aZ é\r\n\t€", lookup)).toEqual([0x61, 0x5a, 0x20, 0xe9, 0xff0d, 0xff09, 0x010020ac]);
  });
  it("types at most MAX_TYPED characters", () => {
    expect(textToKeysyms("x".repeat(MAX_TYPED + 50), lookup)).toHaveLength(MAX_TYPED);
  });
  it("presses combination keys in order and releases them backwards", () => {
    const sent = [];
    sendCombo({ sendKey: (sym, code, down) => sent.push([code, down]) }, KEY_COMBOS.find((c) => c.id === "ctrlAltF2"));
    expect(sent).toEqual([["ControlLeft", true], ["AltLeft", true], ["F2", true], ["F2", false], ["AltLeft", false], ["ControlLeft", false]]);
  });
});
