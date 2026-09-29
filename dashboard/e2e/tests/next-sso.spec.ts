import type { Page } from "@playwright/test";
import { ADMIN, apiLogin, expect, goTo, test, uiLogin } from "../support/fixtures";

// SSO page against the real backend: an incomplete or malformed configuration cannot be enabled,
// changes are tracked, and the client secret is never displayed back.
let token = "";
let original: Record<string, unknown> = {};
const auth = () => ({ Authorization: `Bearer ${token}` });
test.describe.configure({ mode: "serial", timeout: 90_000 });
test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
  original = await (await request.get("/auth/sso/config", { headers: auth() })).json();
});
test.afterAll(async ({ request }) => {
  const { client_secret_set: _ignored, ...rest } = original as { client_secret_set?: boolean };
  await request.put("/auth/sso/config", { headers: auth(), data: { ...rest, enabled: false } }).catch(() => {});
});

async function open(page: Page, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=sso");
}

test("cannot enable an incomplete configuration; saving sends the fields, keeps the secret hidden, and flags unsaved changes", async ({ page, request }) => {
  await open(page);
  const main = page.getByRole("main");
  // The LDAP card below has its own "Save": this one is the OIDC card's.
  const save = main.getByRole("region", { name: "OIDC provider" }).getByRole("button", { name: "Save", exact: true });
  await expect(main.getByRole("heading", { level: 1, name: "Authentication (SSO)" })).toBeVisible({ timeout: 20_000 });
  await expect(main.getByRole("heading", { name: "OIDC provider" })).toBeVisible();
  await expect(save).toBeDisabled(); // nothing changed yet

  await main.getByRole("switch", { name: "Enabled" }).click();
  await expect(save).toBeDisabled();
  await expect(main.getByText("Required to enable SSO.").first()).toBeVisible();
  await expect(main.getByText("Unsaved changes")).toBeVisible();

  await main.getByLabel("Issuer (OIDC discovery URL)").fill("not a url");
  await expect(main.getByText("Enter a full URL")).toBeVisible();
  await main.getByLabel("Issuer (OIDC discovery URL)").fill("https://idp.example.test/realms/hl");
  await main.getByLabel("Client ID").fill("hyperlite");
  await main.getByLabel("Client secret").fill("s3cr3t-value");
  await main.getByLabel("Redirect URL (redirect_uri)").fill("https://hl.example.test/auth/sso/callback");
  await expect(save).toBeEnabled();

  // enabling without any admin group asks for a confirmation first
  await save.click();
  await expect(page.getByRole("alertdialog")).toContainText("Every SSO user will be an observer");
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await main.getByLabel("Identity provider groups → admin role").fill("hyperlite-admins");
  await save.click();
  await expect(main.getByText("Unsaved changes")).toHaveCount(0, { timeout: 15_000 });

  const cfg = await (await request.get("/auth/sso/config", { headers: auth() })).json();
  expect(!!cfg.enabled).toBe(true);
  expect(cfg).toMatchObject({ issuer: "https://idp.example.test/realms/hl", client_id: "hyperlite", admin_groups: "hyperlite-admins", scope: "openid profile email groups" });
  expect(JSON.stringify(cfg)).not.toContain("s3cr3t-value"); // never returned
  await expect(main.getByLabel("Client secret")).toHaveValue("");
  await expect(main.getByText("already saved")).toBeVisible();

  // discard restores the saved values
  await main.getByLabel("Groups claim").fill("roles");
  await main.getByRole("button", { name: "Discard changes" }).click();
  await expect(main.getByLabel("Groups claim")).toHaveValue("groups");
});

test("French labels and no overflow on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await open(page, "fr");
  await expect(page.getByRole("main").getByRole("heading", { level: 1, name: /Authentification/ })).toBeVisible({ timeout: 20_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

// Moved from settings.spec.ts: every test that writes the SSO configuration lives in this serial file, so files
// running in parallel never change it under each other.
test.describe("SSO settings persist and the secret is never returned", () => {
  test("the SSO client secret is stored but never sent back", async ({ request }) => {
    const put = await request.put("/auth/sso/config", { headers: auth(), data: { enabled: false, issuer: "https://idp.example.invalid", client_id: "hyperlite", client_secret: "super-secret-value", redirect_uri: "https://hyperlite.example.invalid/auth/sso/callback", scope: "openid profile email groups", group_claim: "groups", admin_groups: "admins" } });
    expect(put.ok()).toBe(true);
    const cfg = await request.get("/auth/sso/config", { headers: auth() });
    const text = JSON.stringify(await cfg.json());
    expect(text).not.toContain("super-secret-value");
    expect(text).toContain("client_secret_set");
  });

  test("the SSO tab keeps the saved values after a reload without showing the secret", async ({ page }) => {
    await uiLogin(page);
    await goTo(page, "Authentication (SSO)");
    const main = page.getByRole("main");
    await expect(main.getByRole("textbox", { name: /Issuer/ })).toHaveValue("https://idp.example.invalid");
    await expect(main.getByText(/already saved/)).toBeVisible();
    await expect(main.locator("input[type=password]")).toHaveValue("");
    await page.reload();
    await expect(main.getByRole("textbox", { name: /Issuer/ })).toHaveValue("https://idp.example.invalid");
  });
});
