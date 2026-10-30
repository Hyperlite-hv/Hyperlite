import { apiLogin, expect, goTo, test, uiLogin } from "../support/fixtures";

// Administration › API: a button opens Swagger in a new tab; there "Authorize" signs in and the API can be called.
// /docs is never public, and an administrator chooses who may open it.
test("Open Swagger opens /docs in a new tab, Authorize signs in, the access setting closes it", async ({ page, context, browser }) => {
  await uiLogin(page);
  await goTo(page, "API");
  const main = page.getByRole("main");
  await expect(main.getByRole("group", { name: "Who may open Swagger" }).getByRole("button", { name: "Administrators" })).toHaveAttribute("aria-pressed", "true");

  const [swagger] = await Promise.all([context.waitForEvent("page"), main.getByRole("button", { name: "Open Swagger" }).click()]);
  await swagger.waitForURL(/\/docs$/); // the single-use ticket left the address
  await swagger.locator(".opblock-tag").first().waitFor({ timeout: 30_000 });
  await swagger.locator(".auth-wrapper button.authorize").first().click();
  const dialog = swagger.locator(".modal-ux");
  await dialog.locator("#oauth_username").fill(process.env.E2E_ADMIN_USER ?? "admin");
  await dialog.locator("#oauth_password").fill(process.env.E2E_ADMIN_PASSWORD ?? "E2e-Quartz-Harbor-2026");
  await dialog.locator("button.modal-btn.auth.authorize").first().click();
  await expect(dialog.locator("button.modal-btn.auth", { hasText: "Logout" }).first()).toBeVisible();
  await dialog.locator("button.btn-done").first().click();
  await swagger.locator(".opblock-tag", { hasText: "containers" }).first().click();
  await swagger.locator(".opblock-summary", { hasText: "/containers/image-env" }).first().click();
  await swagger.getByRole("button", { name: "Try it out" }).click();
  await swagger.locator("input[placeholder='image']").fill("postgres:16");
  const [response] = await Promise.all([
    swagger.waitForResponse((r) => r.url().includes("/containers/image-env?image=postgres")),
    swagger.getByRole("button", { name: "Execute" }).click(),
  ]);
  expect(response.status()).toBe(200);

  // Without the button, Swagger answers no one.
  const outsider = await (await browser.newContext()).newPage();
  expect((await outsider.goto("/docs"))?.status()).toBe(403);

  // Closed: the open tab loses access at its next page, the button is gone.
  await main.getByRole("button", { name: "Nobody" }).click();
  await expect(main.getByText("Swagger is turned off by an administrator.")).toBeVisible();
  expect((await swagger.reload())?.status()).toBe(403);
  await main.getByRole("button", { name: "Administrators" }).click();
  await expect(main.getByRole("button", { name: "Open Swagger" })).toBeVisible();
});

// hyperlite-admin's lockdown, as ESXi's: root of a node runs it as root@<node>, or named accounts only.
test("The lockdown of hyperlite-admin is turned on and off from the API page", async ({ page, request }) => {
  await uiLogin(page);
  await goTo(page, "API");
  const group = page.getByRole("main").getByRole("group", { name: "Root of a node" });
  await expect(group.getByRole("button", { name: "Signed in as root@<node>" })).toHaveAttribute("aria-pressed", "true");
  const token = await apiLogin(request);
  const lockdown = async () => (await (await request.get("/admin-cli", { headers: { Authorization: `Bearer ${token}` } })).json()).lockdown;
  try {
    await group.getByRole("button", { name: "Lockdown: named accounts only" }).click();
    await expect(group.getByRole("button", { name: "Lockdown: named accounts only" })).toHaveAttribute("aria-pressed", "true");
    expect(await lockdown()).toBe(true);
    await group.getByRole("button", { name: "Signed in as root@<node>" }).click();
    await expect(group.getByRole("button", { name: "Signed in as root@<node>" })).toHaveAttribute("aria-pressed", "true");
    expect(await lockdown()).toBe(false);
  } finally {
    // Another test of this backend may rely on the default.
    await request.put("/admin-cli", { headers: { Authorization: `Bearer ${token}` }, data: { lockdown: false } });
  }
});
