import { ADMIN, expect, seedPreferences, test, uiLogin } from "../support/fixtures";

// Expected noise: the browser logs a console error for every failed HTTP request.
const isExpectedHttpNoise = (p: string) => /Failed to load resource: the server responded with a status of (401|403|404|409|422|429)/.test(p);

test.describe("Authentication", () => {
  test.beforeEach(async ({ page }) => { await seedPreferences(page); });

  test("shows the sign-in page to a visitor without a session", async ({ page, problems }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    await expect(page.getByLabel("Username")).toBeVisible();
    await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
    expect(problems.filter((p) => !isExpectedHttpNoise(p))).toEqual([]);
  });

  test("rejects a wrong password with a clear message and stores no token", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill(ADMIN.username);
    await page.getByLabel("Password", { exact: true }).fill("definitely-wrong");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText(/invalid/i);
    expect(await page.evaluate(() => window.localStorage.getItem("hyperlite_token"))).toBeNull();
    expect(page.url()).not.toMatch(/token|password/i);
  });

  test("rejects an unknown user the same way as a wrong password", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill("e2e-nobody");
    await page.getByLabel("Password", { exact: true }).fill("whatever-123");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText(/invalid/i);
  });

  test("does not submit empty credentials to the backend as a success", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });

  test("the password can be shown and hidden again", async ({ page }) => {
    await page.goto("/");
    const password = page.getByLabel("Password", { exact: true });
    await password.fill("secret-value");
    await expect(password).toHaveAttribute("type", "password");
    await page.getByRole("button", { name: "Show the password" }).click();
    await expect(password).toHaveAttribute("type", "text");
    await page.getByRole("button", { name: "Hide the password" }).click();
    await expect(password).toHaveAttribute("type", "password");
  });

  test("signs in, survives a reload, then signs out", async ({ page, problems }) => {
    await uiLogin(page);
    await page.reload();
    await expect(page.locator(".nx-root")).toBeVisible();
    await page.locator(".nx-sidebar-user").click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    expect(await page.evaluate(() => window.localStorage.getItem("hyperlite_token"))).toBeNull();
    await page.reload();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    expect(problems.filter((p) => !isExpectedHttpNoise(p))).toEqual([]);
  });

  test("an invalid stored token sends the user back to the sign-in page", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => window.localStorage.setItem("hyperlite_token", "not-a-valid-token"));
    await page.reload();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });

  test("a protected deep link asks for sign-in and then lands on the requested page", async ({ page }) => {
    await uiLogin(page, ADMIN.username, ADMIN.password, "/datacenter?tab=storage");
    await expect(page.getByRole("main").getByRole("heading", { level: 1, name: "Storage" })).toBeVisible();
  });

  test("locks the account after repeated failures (brute-force protection)", async ({ request }) => {
    const user = `e2e-lock-${Date.now()}`;
    let last = 0;
    for (let i = 0; i < 7; i++) {
      const r = await request.post("/auth/login", { form: { username: user, password: "bad" } });
      last = r.status();
    }
    expect(last).toBe(429);
  });
});
