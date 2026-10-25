import { expect, test, uiLogin } from "../support/fixtures";

// The Sites page against the real backend (docs/design/multi-cluster.md, step 1): this cluster alone, a search with
// no other cluster to ask, and a cluster that cannot be added because its address does not answer over HTTPS (the
// test backend serves plain HTTP).
test("sites: this cluster, the search, and an address without HTTPS", async ({ page }) => {
  await uiLogin(page);
  await page.goto("/datacenter?tab=sites");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 1, name: "Sites" })).toBeVisible();
  await expect(main.getByText("No other cluster yet")).toBeVisible();
  await expect(main.getByRole("row", { name: /This cluster/ })).toContainText("OK");

  await main.getByLabel("VM name, tag or address").fill("zz-no-such-vm");
  await main.getByRole("button", { name: "Search", exact: true }).click();
  await expect(main.getByText("No VM matches.")).toBeVisible();

  await main.getByRole("button", { name: "Add a cluster" }).click();
  const drawer = page.getByRole("dialog", { name: "Add a cluster" });
  await drawer.getByLabel("Address").fill("127.0.0.1");
  await drawer.getByLabel("Port").fill("9");
  await drawer.getByRole("button", { name: "Read its certificate" }).click();
  await expect(page.getByText("The certificate could not be read")).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Save" })).toBeDisabled();
});
