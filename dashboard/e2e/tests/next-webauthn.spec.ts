import type { Page } from "@playwright/test";
import { apiLogin, expect, PREFIX, test } from "../support/fixtures";

// Security keys (WebAuthn) end to end, with Chromium's virtual authenticator (DevTools protocol) standing in for
// a YubiKey: add a key from Account security, sign in with it instead of a code, remove it with the password.
// WebAuthn needs a secure context and refuses IP addresses, so this file opens the dashboard as localhost.
test.describe.configure({ mode: "serial", timeout: 90_000 });

const USER = `${PREFIX}wa-${Date.now().toString().slice(-6)}`;
const PASSWORD = "E2e-Webauthn-Harbor-2026";

function localhostUrl(baseURL: string | undefined, path = "/") {
  const url = new URL(baseURL || "http://127.0.0.1:8011");
  url.hostname = "localhost";
  url.pathname = path;
  return url.toString();
}

async function signIn(page: Page, baseURL: string | undefined) {
  await page.addInitScript(() => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "en"); } });
  await page.goto(localhostUrl(baseURL));
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(USER);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(PASSWORD);
  await page.getByRole("button", { name: /^(Sign in|Se connecter)$/ }).click();
}

async function virtualAuthenticator(page: Page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "usb", hasResidentKey: false, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  });
}

test.beforeAll(async ({ request }) => {
  const token = await apiLogin(request);
  const r = await request.post("/auth/users", { headers: { Authorization: `Bearer ${token}` }, data: { username: USER, password: PASSWORD, role: "observateur" } });
  expect(r.ok(), await r.text()).toBe(true);
});
test.afterAll(async ({ request }) => {
  const token = await apiLogin(request);
  await request.delete(`/auth/users/${USER}`, { headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
});

test("a security key is added, then signs in instead of a code, then is removed with the password", async ({ page, baseURL }) => {
  await virtualAuthenticator(page);
  await signIn(page, baseURL);
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: new RegExp(`— ${USER}$`) }).click();
  await page.getByRole("menuitem", { name: "Account security" }).click();
  const dialog = page.getByRole("dialog", { name: "Account security" });
  const keys = dialog.getByRole("region", { name: "Security keys and passkeys" });
  await expect(keys.getByText("No security key.")).toBeVisible();
  await keys.getByLabel("Key name").fill("Virtual key");
  await keys.getByRole("button", { name: "Add a key" }).click();
  await expect(keys.getByText("Virtual key", { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(keys.getByText(/^localhost · added/)).toBeVisible();
  await page.keyboard.press("Escape");

  // Sign out, sign in again: the key is asked for, and the virtual authenticator answers it.
  await page.getByRole("button", { name: new RegExp(`— ${USER}$`) }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await signIn(page, baseURL);
  await expect(page.getByRole("heading", { name: "Two-step verification" })).toBeVisible();
  await expect(page.getByLabel("6-digit verification code")).toHaveCount(0); // no TOTP on this account: only the key is offered
  await page.getByRole("button", { name: "Use a security key" }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });

  // Removing the key takes the password; a wrong one changes nothing.
  await page.getByRole("button", { name: new RegExp(`— ${USER}$`) }).click();
  await page.getByRole("menuitem", { name: "Account security" }).click();
  await keys.getByRole("button", { name: "Remove the key Virtual key" }).click();
  await page.getByLabel("Your password").fill("wrong-password");
  await page.getByRole("button", { name: "Remove", exact: true }).click();
  await expect(page.getByText("Incorrect password").first()).toBeVisible({ timeout: 20_000 });
  await expect(keys.getByText("Virtual key", { exact: true })).toBeVisible();
  await keys.getByRole("button", { name: "Remove the key Virtual key" }).click();
  await page.getByLabel("Your password").fill(PASSWORD);
  await page.getByRole("button", { name: "Remove", exact: true }).click();
  await expect(keys.getByText("No security key.")).toBeVisible({ timeout: 20_000 });
});

test("the sign-in step on an IP address says why a key cannot be used there", async ({ page, request }) => {
  // Stand-in: an account with a key signing in through 127.0.0.1 (the real key above was removed).
  await page.route(/\/auth\/login$/, (route) => route.fulfill({ json: { require_2fa: true, pre_auth_token: "x", methods: ["webauthn", "totp"] } }));
  await page.addInitScript(() => { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "fr"); });
  await page.goto("/");
  await page.getByLabel(/Nom d.utilisateur/).fill(USER);
  await page.getByLabel(/^Mot de passe$/).fill(PASSWORD);
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("button", { name: "Utiliser une clé de sécurité" })).toBeDisabled();
  await expect(page.getByText(/^Ce navigateur ne peut pas utiliser de clé de sécurité ici/)).toBeVisible();
  await expect(page.getByLabel("Code de vérification à 6 chiffres")).toBeVisible(); // the code still works
  expect((await request.get("/health")).ok()).toBe(true);
});
