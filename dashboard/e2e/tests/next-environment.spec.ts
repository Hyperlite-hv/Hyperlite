import { expect, test, uiLogin, seedPreferences } from "../support/fixtures";

// A non-production instance (HYPERLITE_ENV_LABEL=DEV on the server) says so on the sign-in page, in the top bar
// and in the tab title. The test machine has no label: /health is given one.
test("a labelled instance shows its label everywhere, an unlabelled one nothing", async ({ page }) => {
  await page.route("**/health", async (route) => {
    const res = await route.fetch();
    return route.fulfill({ response: res, json: { ...(await res.json()), environment: "DEV" } });
  });
  await seedPreferences(page);
  await page.goto("/");
  await expect(page.locator(".nx-login .nx-envbadge")).toHaveText("DEV");
  await expect(page).toHaveTitle(/^\[DEV\] /);
  await uiLogin(page);
  await expect(page.locator(".nx-top .nx-envbadge")).toHaveText("DEV");
  await expect(page).toHaveTitle(/^\[DEV\] Hyperlite/);
});

test("without a label, nothing is added", async ({ page }) => {
  await uiLogin(page);
  await expect(page.locator(".nx-envbadge")).toHaveCount(0);
  await expect(page).not.toHaveTitle(/^\[/);
});
