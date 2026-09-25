import type { Page } from "@playwright/test";
import { ADMIN, expect, test } from "../support/fixtures";

// Node pages of the local host against the real backend.
test.describe.configure({ mode: "serial", timeout: 90_000 });

async function open(page: Page, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/Password|Mot de passe/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
}

test("local node: system, network, disk, tasks, compatibility and shell pages show real data", async ({ page }) => {
  await open(page);
  await page.goto("/node/local?tab=system");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "System", exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(main.getByText("Kernel")).toBeVisible();
  await expect(main.getByText("libvirt hostname")).toBeVisible();

  await page.goto("/node/local?tab=network");
  await expect(main.getByRole("table").getByRole("row").nth(1)).toBeVisible({ timeout: 20_000 });
  await expect(main.getByRole("columnheader", { name: "Bridge" })).toBeVisible();

  await page.goto("/node/local?tab=disk");
  await expect(main.getByRole("table").getByRole("row", { name: /default/ })).toBeVisible({ timeout: 20_000 });

  // tasks are scoped to the node: the request carries it
  const req = page.waitForRequest((r) => /\/tasks\?/.test(r.url()) && r.url().includes("node=local"));
  await page.goto("/node/local?tab=tasks");
  await req;

  await page.goto("/node/local?tab=compat");
  await expect(main.getByRole("heading", { name: "Features" })).toBeVisible({ timeout: 30_000 });
  await expect(main.getByRole("heading", { name: "Preflight check" })).toBeVisible();

  await page.goto("/node/local?tab=shell");
  await expect(main.getByText("Root-equivalent access")).toBeVisible();
  const [popup] = await Promise.all([page.context().waitForEvent("page"), main.getByRole("button", { name: "Open in a new window" }).click()]);
  expect(popup.url()).toContain("/host-shell");
  await popup.close();
});

test("French labels and no overflow on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await open(page, "fr");
  await page.goto("/node/local?tab=compat");
  await expect(page.getByRole("main").getByRole("heading", { name: "Fonctionnalités" })).toBeVisible({ timeout: 30_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
