import { expect, test, uiLogin } from "../support/fixtures";

// Renaming the local node changes the machine's host name: the request is answered here, so the test host keeps
// its own name. The backend part is covered by tests/test_host_rename.py.
test("the local node is renamed from its Actions menu, with the host name checked first", async ({ page }) => {
  const sent: unknown[] = [];
  await page.route(/\/host\/system\/hostname$/, (r) => {
    sent.push(r.request().postDataJSON());
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ nom: "pve1.home", ancien: "old" }) });
  });
  await uiLogin(page);
  await page.goto("/node/local?tab=summary");
  await page.getByRole("main").getByRole("button", { name: "Actions" }).click();
  const item = page.getByRole("menuitem", { name: /Rename/ });
  await expect(item).not.toHaveAttribute("aria-disabled", "true");
  await item.click();
  const prompt = page.getByRole("dialog");
  await expect(prompt).toContainText("host name");
  await prompt.getByLabel("New name").fill("bad name");
  await prompt.getByRole("button", { name: "Rename" }).click();
  await expect(prompt.getByRole("alert")).toContainText("dot-separated");
  await prompt.getByLabel("New name").fill("pve1.home");
  await prompt.getByRole("button", { name: "Rename" }).click();
  await expect(page.getByText("Node renamed")).toBeVisible();
  expect(sent).toEqual([{ nom: "pve1.home" }]);
  await expect(page).toHaveURL(/\/node\/local/);
});
