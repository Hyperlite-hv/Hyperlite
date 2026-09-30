import { expect, goTo, test, uiLogin } from "../support/fixtures";

// Administration › API: the interactive documentation inside the dashboard, its access setting and the account's
// API tokens. Swagger's "Try it out" is sent with the session of the signed-in user.
test("the API page shows Swagger with the session, its access setting and the account's tokens", async ({ page }) => {
  await uiLogin(page);
  await goTo(page, "API");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 1, name: "API" })).toBeVisible();
  await expect(main.getByRole("group", { name: "Who may open the API documentation" }).getByRole("button", { name: "Administrators" })).toHaveAttribute("aria-pressed", "true");

  const swagger = main.locator(".nx-swagger");
  await swagger.locator(".opblock-tag", { hasText: "containers" }).first().click({ timeout: 30_000 });
  await swagger.locator(".opblock-summary", { hasText: "/containers/image-env" }).first().click();
  await swagger.getByRole("button", { name: "Try it out" }).click();
  await swagger.locator("input[placeholder='image']").fill("postgres:16");
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/containers/image-env?image=postgres")),
    swagger.getByRole("button", { name: "Execute" }).click(),
  ]);
  expect(response.status()).toBe(200);
  expect(response.request().headers().authorization).toMatch(/^Bearer /);

  // A token is shown once, then only listed.
  const tokens = main.getByRole("region", { name: "My API tokens" });
  await tokens.getByLabel("Name").fill("e2e-api-page");
  await tokens.getByRole("button", { name: "Create a token" }).click();
  await expect(tokens.getByText("Copy it now: it will never be shown again.")).toBeVisible();
  await expect(tokens.getByRole("row", { name: /e2e-api-page/ })).toBeVisible();
  await tokens.getByRole("button", { name: "Revoke token e2e-api-page" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Revoke" }).click();
  await expect(tokens.getByRole("row", { name: /e2e-api-page/ })).toHaveCount(0);

  // Closed: the documentation says so, the schema is refused.
  await main.getByRole("button", { name: "Nobody" }).click();
  await expect(main.getByText("The API documentation is turned off by an administrator.")).toBeVisible();
  const schema = await page.evaluate(async () => (await fetch("/api-docs/schema", { headers: { Authorization: `Bearer ${localStorage.getItem("hyperlite_token")}` } })).status);
  expect(schema).toBe(403);
  await main.getByRole("button", { name: "Administrators" }).click();
  await expect(main.locator(".nx-swagger")).toBeVisible();
});
