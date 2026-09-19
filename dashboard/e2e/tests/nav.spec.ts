import { expect, test, uiLogin } from "../support/fixtures";

const isExpectedHttpNoise = (p: string) => /Failed to load resource/.test(p);

test.describe("Navigation and tab persistence", () => {
  test("keeps the selected Datacenter tab after a reload and reflects it in the URL", async ({ page }) => {
    await uiLogin(page);
    await page.getByRole("button", { name: "Backups", exact: true }).first().click();
    await expect(page).toHaveURL(/tab=backups/);
    await page.reload();
    await expect(page).toHaveURL(/tab=backups/);
    await expect(page.getByRole("tab", { name: "Backups" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText("No backups yet.")).toBeVisible();
  });

  test("returns to the summary tab when another resource is selected", async ({ page, request }) => {
    const { hostname } = await (await request.get("/health")).json();
    await uiLogin(page);
    await page.getByRole("button", { name: "Storage", exact: true }).first().click();
    await expect(page).toHaveURL(/tab=storage/);
    await page.getByRole("treeitem", { name: new RegExp(hostname.split(".")[0]) }).click();
    await expect(page).toHaveURL(/\/node\//);
    await expect(page).not.toHaveURL(/tab=/);
    await page.getByRole("treeitem", { name: "Datacenter" }).click();
    await expect(page.getByText("VM status")).toBeVisible();
  });

  test("an unknown tab in the URL falls back to the summary", async ({ page, problems }) => {
    await uiLogin(page);
    await page.goto("/datacenter?tab=does-not-exist");
    await expect(page.getByText("VM status")).toBeVisible();
    expect(problems.filter((p) => !isExpectedHttpNoise(p))).toEqual([]);
  });

  test("an unknown route redirects to the datacenter instead of a blank page", async ({ page }) => {
    await uiLogin(page);
    await page.goto("/this/does/not/exist");
    await expect(page).toHaveURL(/\/datacenter/);
    await expect(page.getByText("VM status")).toBeVisible();
  });
});
