import { expect, test as base, type APIRequestContext, type Page } from "@playwright/test";

export const ADMIN = { username: "admin", password: process.env.E2E_ADMIN_PASSWORD ?? "E2e-Admin-2026" };
export const PREFIX = "e2e-";

/** Console messages of level error and uncaught exceptions collected during a test. */
export type BrowserProblems = string[];

export async function apiLogin(request: APIRequestContext, username = ADMIN.username, password = ADMIN.password) {
  const res = await request.post("/auth/login", { form: { username, password } });
  expect(res.ok(), `login as ${username}`).toBeTruthy();
  const body = await res.json();
  return body.access_token as string;
}

export async function uiLogin(page: Page, username = ADMIN.username, password = ADMIN.password) {
  await page.goto("/");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Password hashing is CPU bound: allow for a slow, loaded test host.
  await expect(page.getByText("Datacenter").first()).toBeVisible({ timeout: 30_000 });
}

export const test = base.extend<{ problems: BrowserProblems }>({
  problems: async ({ page }, use) => {
    const problems: BrowserProblems = [];
    page.on("pageerror", (e) => {
      // WebKit reports a fetch cancelled by a navigation or reload this way; it is not an application error.
      if (/due to access control checks/.test(e.message)) return;
      problems.push(`pageerror: ${e.message}`);
    });
    page.on("console", (m) => {
      if (m.type() === "error") problems.push(`console: ${m.text()}`);
    });
    await use(problems);
  },
});
export { expect };
