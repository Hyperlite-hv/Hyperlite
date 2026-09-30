import { apiLogin, expect, goTo, test, uiLogin } from "../support/fixtures";

// Renaming what is only a label: a user group keeps its members and permissions under its new name. The backend
// part, with storage pools, networks, templates and ISOs, is covered by tests/test_rename_more.py.
test("a user group is renamed from its card, its members stay", async ({ page, request }) => {
  const token = await apiLogin(request);
  const headers = { Authorization: `Bearer ${token}` };
  const name = `e2e-grp-${Date.now() % 100000}`;
  const created = await request.post("/groups", { headers, data: { name } });
  const id = (await created.json()).id;
  await request.post(`/groups/${id}/members`, { headers, data: { username: "admin" } });

  await uiLogin(page);
  await goTo(page, "Users and roles");
  const main = page.getByRole("main");
  await main.getByRole("tab", { name: /^Groups/ }).click();
  await main.getByRole("button", { name: `Rename ${name}` }).click();
  const prompt = page.getByRole("dialog");
  await prompt.getByLabel("New name").fill(`${name}-ops`);
  await prompt.getByRole("button", { name: "Rename" }).click();
  const card = main.getByRole("region", { name: `${name}-ops` });
  await expect(card).toBeVisible();
  await expect(card.getByText("admin")).toBeVisible();

  await request.delete(`/groups/${id}`, { headers });
});
