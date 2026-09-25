import type { Page } from "@playwright/test";
import { ADMIN, apiLogin, expect, PREFIX, test } from "../support/fixtures";

// Notifications page against the real backend (the webhook target is a closed local port, so the
// test action must fail cleanly and report the reason).
const stamp = Date.now().toString().slice(-6);
const HOOK = `${PREFIX}hook-${stamp}`;
const MAIL = `${PREFIX}mail-${stamp}`;
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });
test.describe.configure({ mode: "serial", timeout: 90_000 });
test.beforeAll(async ({ request }) => { token = await apiLogin(request); });
test.afterAll(async ({ request }) => {
  const chans = (await (await request.get("/notifications/channels", { headers: auth() })).json()) as { id: number; name: string }[];
  for (const c of chans) if ([HOOK, MAIL].includes(c.name)) await request.delete(`/notifications/channels/${c.id}`, { headers: auth() }).catch(() => {});
});

async function open(page: Page, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/Password|Mot de passe/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=notifications");
}

test("webhook and email channels: validation, creation with events, test failure, toggle, confirmed deletion", async ({ page, request }) => {
  await open(page);
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 2, name: /^Notification channels/ })).toBeVisible({ timeout: 20_000 });
  await main.getByRole("button", { name: "Add a channel" }).click();

  // nothing is sent while the form is invalid, and each error sits next to its field
  await main.getByRole("button", { name: "Create", exact: true }).click();
  await expect(main.getByText("Required.").first()).toBeVisible();
  await main.getByLabel("Channel name").fill(HOOK);
  await main.getByLabel("Webhook URL").fill("ftp://nope");
  await main.getByRole("button", { name: "Create", exact: true }).click();
  await expect(main.getByText("Enter a full URL starting with http")).toBeVisible();
  await main.getByLabel("Webhook URL").fill("http://127.0.0.1:9/hook");
  await main.getByRole("checkbox").first().check();
  await main.getByRole("button", { name: "Create", exact: true }).click();
  const row = main.getByRole("row", { name: new RegExp(HOOK) });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row).toContainText("Active");
  const chans = (await (await request.get("/notifications/channels", { headers: auth() })).json()) as { name: string; events: string[] }[];
  expect(chans.find((c) => c.name === HOOK)?.events).toHaveLength(1);

  // email form validates addresses and port
  await main.getByRole("button", { name: "Add a channel" }).click();
  await main.getByRole("button", { name: "Email (SMTP)" }).click();
  await main.getByLabel("Channel name").fill(MAIL);
  await main.getByLabel("SMTP server").fill("smtp.invalid");
  await main.getByLabel("Port").fill("99999");
  await main.getByLabel("Sender (From)").fill("nope");
  await main.getByLabel("Recipient (To)").fill("a@b.co");
  await main.getByRole("button", { name: "Create", exact: true }).click();
  await expect(main.getByText("Enter a port between 1 and 65535.")).toBeVisible();
  await expect(main.getByText("Enter a valid email address.")).toHaveCount(1);
  await main.getByRole("button", { name: "Cancel" }).click();

  // the test reports the failure instead of pretending, then toggling and deleting
  await row.getByRole("button", { name: `Test ${HOOK}` }).click();
  await expect(page.getByText("Test failed").first()).toBeVisible({ timeout: 20_000 });
  await row.getByRole("button", { name: `Disable ${HOOK}` }).click();
  await expect(row).toContainText("Inactive");
  await row.getByRole("button", { name: `Delete channel ${HOOK}` }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: `Delete channel ${HOOK}` }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(row).toHaveCount(0, { timeout: 15_000 });
});

test("French labels and no overflow on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await open(page, "fr");
  await expect(page.getByRole("main").getByRole("heading", { level: 2, name: /Canaux de notification/ })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("main").getByRole("button", { name: "Ajouter un canal" }).click();
  await page.getByRole("main").getByRole("button", { name: "E-mail (SMTP)" }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
