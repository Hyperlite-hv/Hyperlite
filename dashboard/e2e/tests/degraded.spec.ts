import { expect, goTo, seedPreferences, test, uiLogin } from "../support/fixtures";

const json = (status: number, body: unknown) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

test.describe("Degraded backend behavior", () => {
  test("a rejected session token returns the user to the sign-in page with an explanation", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/storage", (r) => r.fulfill(json(401, { detail: "Could not validate credentials" })));
    await goTo(page, "Storage");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText(/session has expired/i);
    expect(await page.evaluate(() => window.localStorage.getItem("hyperlite_token"))).toBeNull();
  });

  test("a server error is reported with the backend message and the page stays usable", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/isos", (r) => r.fulfill(json(500, { detail: "Internal failure" })));
    await goTo(page, "ISO images and templates");
    await expect(page.getByText("Internal failure").first()).toBeVisible();
    await goTo(page, "Network");
    await expect(page.getByRole("button", { name: "Create a network" })).toBeVisible();
  });

  test("a malformed response gives a readable message, not a JavaScript error", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/networks", (r) => r.fulfill({ status: 200, contentType: "application/json", body: "{not json" }));
    await goTo(page, "Network");
    await expect(page.getByText("The server returned an unreadable response.").first()).toBeVisible();
    await expect(page.getByText(/Cannot read properties/)).toHaveCount(0);
  });

  test("losing the network shows a clear message and the page recovers when it returns", async ({ page }) => {
    await uiLogin(page);
    // Aborting the request behaves the same in every browser; context.setOffline does not in Firefox.
    await page.route("**/networks", (route) => route.abort("connectionrefused"));
    await goTo(page, "Network");
    await expect(page.getByText(/Cannot reach the server/).first()).toBeVisible();
    await page.unroute("**/networks");
    await page.reload();
    await expect(page.getByRole("button", { name: "Create a network" })).toBeVisible();
  });

  test("a load that never completes explains itself instead of spinning silently", async ({ page }) => {
    await page.clock.install();
    await uiLogin(page);
    await page.route("**/networks", () => undefined); // request never answered
    await goTo(page, "Network");
    const main = page.getByRole("main");
    await expect(main.getByRole("status").filter({ hasText: "Loading" }).first()).toBeVisible();
    await page.clock.fastForward(11_000);
    await expect(main.getByText(/taking longer than expected/)).toBeVisible();
    await expect(main.getByRole("button", { name: "Reload the page" })).toBeVisible();
  });

  test("a slow response shows the loading state, then the content", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/networks", async (route) => {
      await new Promise((r) => setTimeout(r, 1500));
      await route.continue();
    });
    await goTo(page, "Network");
    await expect(page.getByRole("main").getByRole("status").filter({ hasText: "Loading" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Create a network" })).toBeVisible();
  });

  test("a forbidden action shows the reason and keeps what the user typed", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/networks", (route) => (route.request().method() === "POST" ? route.fulfill(json(403, { detail: "Insufficient privileges" })) : route.continue()));
    await goTo(page, "Network");
    await page.getByRole("button", { name: "Create a network" }).click();
    const drawer = page.getByRole("dialog", { name: "Create a network" });
    await drawer.getByRole("textbox", { name: "Name", exact: true }).fill("e2e-keep-me");
    await drawer.getByRole("button", { name: "Create a network", exact: true }).click();
    await expect(page.getByText("Insufficient privileges").first()).toBeVisible();
    await expect(drawer.getByRole("textbox", { name: "Name", exact: true })).toHaveValue("e2e-keep-me");
  });

  test("a conflict (409) is shown as a readable message", async ({ page }) => {
    await uiLogin(page);
    await page.route("**/networks", (route) => {
      if (route.request().method() !== "POST") return route.continue();
      return route.fulfill(json(409, { detail: "A network with this name already exists" }));
    });
    await goTo(page, "Network");
    await page.getByRole("button", { name: "Create a network" }).click();
    const drawer = page.getByRole("dialog", { name: "Create a network" });
    await drawer.getByRole("textbox", { name: "Name", exact: true }).fill("e2e-conflict");
    await drawer.getByRole("button", { name: "Create a network", exact: true }).click();
    await expect(page.getByText("A network with this name already exists").first()).toBeVisible();
  });

  test("too many failed sign-ins are reported as a rate limit", async ({ page, request }) => {
    const user = `e2e-rate-${Date.now()}`;
    for (let i = 0; i < 6; i++) await request.post("/auth/login", { form: { username: user, password: "bad" } });
    await seedPreferences(page);
    await page.goto("/");
    await page.getByLabel("Username").fill(user);
    await page.getByLabel("Password", { exact: true }).fill("bad");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toContainText(/too many|try again|locked/i);
  });

  test("the sign-in request is not sent twice when the button is double-clicked", async ({ page }) => {
    let logins = 0;
    page.on("request", (r) => {
      if (r.url().endsWith("/auth/login") && r.method() === "POST") logins += 1;
    });
    await seedPreferences(page);
    await page.goto("/");
    await page.getByLabel("Username").fill("admin");
    await page.getByLabel("Password", { exact: true }).fill("E2e-Quartz-Harbor-2026");
    await page.getByRole("button", { name: "Sign in" }).dblclick();
    await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
    expect(logins).toBe(1);
  });
});
