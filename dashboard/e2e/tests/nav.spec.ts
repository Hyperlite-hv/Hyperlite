import { expect, goTo, test, uiLogin } from "../support/fixtures";

const isExpectedHttpNoise = (p: string) => /Failed to load resource/.test(p);

test.describe("Navigation and tab persistence", () => {
  test("keeps the selected Datacenter page after a reload and reflects it in the URL", async ({ page }) => {
    await uiLogin(page);
    await goTo(page, "Backups");
    await expect(page).toHaveURL(/tab=backups/);
    await page.reload();
    await expect(page).toHaveURL(/tab=backups/);
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("button", { name: "Backups", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("main").getByRole("heading", { level: 1, name: "Backups" })).toBeVisible();
    await expect(page.getByText("No backups yet.")).toBeVisible();
  });

  test("returns to the summary tab when another resource is selected", async ({ page }) => {
    await uiLogin(page);
    await goTo(page, "Storage");
    await expect(page).toHaveURL(/tab=storage/);
    await goTo(page, "Nodes");
    await page.getByRole("main").getByRole("table").getByRole("button").first().click();
    await expect(page).toHaveURL(/\/node\//);
    await expect(page).not.toHaveURL(/tab=/);
    await expect(page.getByRole("main").getByRole("tab", { name: "Summary", exact: true })).toHaveAttribute("aria-selected", "true");
    await goTo(page, "Home");
    await expect(page.getByRole("main").getByRole("group", { name: "Inventory" })).toBeVisible();
  });

  test("an unknown tab in the URL falls back to the summary", async ({ page, problems }) => {
    await uiLogin(page);
    await page.goto("/datacenter?tab=does-not-exist");
    await expect(page.getByRole("main").getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
    expect(problems.filter((p) => !isExpectedHttpNoise(p))).toEqual([]);
  });

  test("an unknown route redirects to the datacenter instead of a blank page", async ({ page }) => {
    await uiLogin(page);
    await page.goto("/this/does/not/exist");
    await expect(page).toHaveURL(/\/datacenter/);
    await expect(page.getByRole("main").getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
  });
});
