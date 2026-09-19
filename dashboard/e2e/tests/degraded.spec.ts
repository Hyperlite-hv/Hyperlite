import { expect, test, uiLogin } from "../support/fixtures";

const json = (status: number, body: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

test.describe("Degraded backend behavior", () => {
  test("a rejected session token returns the user to the sign-in page with an explanation", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/storage", (r) => r.fulfill(json(401, { detail: "Could not validate credentials" })));
    await page.getByRole("tab", { name: "Storage", exact: true }).click();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText(/session has expired/i);
    expect(await page.evaluate(() => window.localStorage.getItem("hyperlite_token"))).toBeNull();
  });

  test("a server error is reported with the backend message and the page stays usable", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/isos", (r) => r.fulfill(json(500, { detail: "Internal failure" })));
    await page.getByRole("tab", { name: "Storage", exact: true }).click();
    await expect(page.getByText("Internal failure")).toBeVisible();
    await page.getByRole("tab", { name: "Network", exact: true }).click();
    await expect(page.getByRole("button", { name: "Create a virtual network" })).toBeVisible();
  });

  test("a malformed response gives a readable message, not a JavaScript error", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/networks", (r) => r.fulfill({ status: 200, contentType: "application/json", body: "{not json" }));
    await page.getByRole("tab", { name: "Network", exact: true }).click();
    await expect(page.getByText("The server returned an unreadable response.")).toBeVisible();
    await expect(page.getByText(/Cannot read properties/)).toHaveCount(0);
  });

  test("losing the network shows a clear message and the page recovers when it returns", async ({ page, context }) => {
    await uiLogin(page);
    await context.setOffline(true);
    await page.getByRole("tab", { name: "Network", exact: true }).click();
    await expect(page.getByText(/Cannot reach the server/)).toBeVisible();
    await context.setOffline(false);
    await page.reload();
    await expect(page.getByRole("button", { name: "Create a virtual network" })).toBeVisible();
  });

  test("a load that never completes explains itself instead of spinning silently", async ({ page }) => {
    await page.clock.install();
    await uiLogin(page);
    await page.route("**/networks", () => undefined); // request never answered
    await page.getByRole("tab", { name: "Network", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("Loading");
    await page.clock.fastForward(11_000);
    await expect(page.getByRole("status")).toContainText(/taking longer than expected/);
    await expect(page.getByRole("button", { name: "Reload the page" })).toBeVisible();
  });

  test("a slow response shows the loading state, then the content", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/networks", async (route) => {
      await new Promise((r) => setTimeout(r, 1500));
      await route.continue();
    });
    await page.getByRole("tab", { name: "Network", exact: true }).click();
    await expect(page.getByRole("status")).toBeVisible();
    await expect(page.getByRole("button", { name: "Create a virtual network" })).toBeVisible();
  });

  test("a forbidden action shows the reason and keeps what the user typed", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/networks", (route) => (route.request().method() === "POST" ? route.fulfill(json(403, { detail: "Insufficient privileges" })) : route.continue()));
    await page.getByRole("tab", { name: "Network", exact: true }).click();
    await page.getByRole("button", { name: "Create a virtual network" }).click();
    await page.getByRole("textbox", { name: "Name", exact: true }).fill("e2e-keep-me");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByText("Insufficient privileges")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Name", exact: true })).toHaveValue("e2e-keep-me");
  });

  test("a conflict (409) and a validation error (422) are shown as readable messages", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/networks", (route) => {
      if (route.request().method() !== "POST") return route.continue();
      return route.fulfill(json(409, { detail: "A network with this name already exists" }));
    });
    await page.getByRole("tab", { name: "Network", exact: true }).click();
    await page.getByRole("button", { name: "Create a virtual network" }).click();
    await page.getByRole("textbox", { name: "Name", exact: true }).fill("e2e-conflict");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByText("A network with this name already exists")).toBeVisible();
  });

  test("too many failed sign-ins are reported as a rate limit", async ({ page, request }) => {
    const user = `e2e-rate-${Date.now()}`;
    for (let i = 0; i < 6; i++) await request.post("/auth/login", { form: { username: user, password: "bad" } });
    await page.goto("/");
    await page.getByLabel("Username").fill(user);
    await page.getByLabel("Password").fill("bad");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText(/too many|try again|locked/i);
  });

  test("the sign-in request is not sent twice when the button is double-clicked", async ({ page }) => {
    let logins = 0;
    page.on("request", (r) => {
      if (r.url().endsWith("/auth/login") && r.method() === "POST") logins += 1;
    });
    await page.goto("/");
    await page.getByLabel("Username").fill("admin");
    await page.getByLabel("Password").fill("E2e-Admin-2026");
    await page.getByRole("button", { name: "Sign in" }).dblclick();
    await expect(page.getByText("Datacenter").first()).toBeVisible();
    expect(logins).toBe(1);
  });
});
