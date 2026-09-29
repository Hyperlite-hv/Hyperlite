import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// My preferences (account menu): the terminal font size and the storage pools the Home page follows, kept in the
// browser across a reload. The pools are served by the test.
const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const pool = (nom: string) => ({ nom, type: "dir", etat: "actif", capacite_go: 100, disponible_go: 60, chemin: `/srv/${nom}` });

test("preferences: terminal size and the pools shown on the Home page", async ({ page }) => {
  await uiLogin(page);
  await page.route(/\/storage$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([pool("e2e-fast"), pool("e2e-slow")])) : r.fallback()));
  await page.goto("/datacenter?tab=summary");
  const main = page.getByRole("main");
  const pools = main.getByRole("region", { name: "Storage pools" });
  await expect(pools.getByText("e2e-slow", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: /^Account/ }).click();
  await page.getByRole("menuitem", { name: "My preferences" }).click();
  const drawer = page.getByRole("dialog", { name: "My preferences" });
  await drawer.getByLabel("Size").selectOption("18");
  await expect(drawer.getByLabel("Preview")).toHaveCSS("font-size", "18px");
  await drawer.getByRole("checkbox", { name: /^e2e-slow/ }).uncheck();
  await drawer.getByRole("button", { name: "Done" }).click();
  await expect(pools.getByText("e2e-slow", { exact: true })).toHaveCount(0);
  await expect(pools.getByText("e2e-fast", { exact: true })).toBeVisible();

  await page.reload();
  await expect(main.getByRole("region", { name: "Storage pools" }).getByText("e2e-fast", { exact: true })).toBeVisible();
  await expect(main.getByRole("region", { name: "Storage pools" }).getByText("e2e-slow", { exact: true })).toHaveCount(0);
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("hyperlite-next-prefs") || "{}"));
  expect(stored).toEqual({ termFont: "plex", termFontSize: 18, overviewPools: ["local:e2e-fast"] });
});
