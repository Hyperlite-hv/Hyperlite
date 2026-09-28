import { expect, test as base, type APIRequestContext, type Page } from "@playwright/test";

export const ADMIN = { username: "admin", password: process.env.E2E_ADMIN_PASSWORD ?? "E2e-Quartz-Harbor-2026" };
export const PREFIX = "e2e-";

/** Console messages of level error and uncaught exceptions collected during a test. */
export type BrowserProblems = string[];

export async function apiLogin(request: APIRequestContext, username = ADMIN.username, password = ADMIN.password) {
  const res = await request.post("/auth/login", { form: { username, password } });
  expect(res.ok(), `login as ${username}`).toBeTruthy();
  const body = await res.json();
  return body.access_token as string;
}

/** English and the dark theme unless a test chose otherwise (kept across reloads within a test). */
export async function seedPreferences(page: Page, { lang = "en", theme = "dark" } = {}) {
  await page.addInitScript(([lg, th]) => {
    if (!localStorage.getItem("hyperlite-next-lang")) localStorage.setItem("hyperlite-next-lang", lg);
    if (!localStorage.getItem("hyperlite-next-theme")) localStorage.setItem("hyperlite-next-theme", th);
  }, [lang, theme]);
}

/** Signs in through the sign-in screen (optionally from a deep link) and waits for the dashboard. */
export async function uiLogin(page: Page, username = ADMIN.username, password = ADMIN.password, path = "/") {
  await seedPreferences(page);
  await page.goto(path);
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Password hashing is CPU bound: allow for a slow, loaded test host.
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
}

/** Opens a page of the sidebar by its label (an entry may end with its count, as "Nodes 2"). */
export async function goTo(page: Page, label: string) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: new RegExp(`^${escaped}( \\d+)?$`) }).click();
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
