import { describe, expect, it } from "vitest";
import { generatePassword, passwordAccepted, passwordChecks } from "../next/lib/passwordPolicy";

// Same cases as tests/test_password_change.py: the dashboard's checklist must agree with the server.
describe("password policy (mirror of app/core/password_policy.py)", () => {
  const refused = {
    "short-one": "length",
    ["é".repeat(37)]: "length",
    "my-alice-account-pw": "name",
    abababababab: "variety",
    "123456789012": "variety",
    azertyuiopqs: "variety",
    "P@ssw0rd2024!": "common",
    "Azerty123456!": "common",
    Hyperlite2026: "common",
  };
  for (const [pw, rule] of Object.entries(refused)) {
    it(`refuses ${JSON.stringify(pw)} (${rule})`, () => {
      expect(passwordAccepted(pw, "alice")).toBe(false);
      expect(passwordChecks(pw, "alice").find((c) => c.id === rule).ok).toBe(false);
    });
  }
  for (const pw of ["Tangerine-Orbit-42", "correct horse battery staple", "Vélo-du-matin-7"]) {
    it(`accepts ${JSON.stringify(pw)}`, () => expect(passwordAccepted(pw, "alice")).toBe(true));
  }
  it("generates passwords that pass and differ", () => {
    const a = generatePassword("alice");
    const b = generatePassword("alice");
    expect(passwordAccepted(a, "alice")).toBe(true);
    expect(a).toMatch(/^[A-Za-z2-9]{5}(-[A-Za-z2-9]{5}){3}$/);
    expect(a).not.toBe(b);
  });
});
