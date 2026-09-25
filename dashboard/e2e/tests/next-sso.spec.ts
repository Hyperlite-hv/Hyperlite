import type { Page } from "@playwright/test";
import { ADMIN, apiLogin, expect, test } from "../support/fixtures";

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
  await page.getByLabel(/Password|Mot de passe/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=sso");
}

test("cannot enable an incomplete configuration; saving sends the fields, keeps the secret hidden, and flags unsaved changes", async ({ page, request }) => {
  await open(page);
  const main = page.getByRole("main");
  const save = main.getByRole("button", { name: "Save", exact: true });
  await expect(main.getByRole("heading", { name: "Single sign-on (OIDC)" })).toBeVisible({ timeout: 20_000 });
  await expect(save).toBeDisabled(); // nothing changed yet

  await main.getByRole("switch").check();
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
  await main.getByLabel("IdP groups → admin role").fill("hyperlite-admins");
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
  await expect(page.getByRole("main").getByRole("heading", { level: 2, name: /Authentification unique/ })).toBeVisible({ timeout: 20_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
