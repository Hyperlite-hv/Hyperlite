import type { Page } from "@playwright/test";
import { ADMIN, expect, test } from "../support/fixtures";

// Sign-in of the hyperlite workstation client: the code is approved from a web session, on the /cli-login page.
test.describe.configure({ mode: "serial", timeout: 90_000 });

async function signIn(page: Page, path: string) {
  await page.addInitScript(() => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "en"); } });
  await page.goto(path);
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("a workstation code is approved after signing in, then hands out an expiring token once", async ({ page, request }) => {
  const start = await (await request.post("/auth/cli/start", { data: { hostname: "E2E-WORKSTATION", client_version: "e2e" } })).json();
  const poll = () => request.post("/auth/cli/token", { data: { device_code: start.device_code } });
  expect((await (await poll()).json()).error).toBe("authorization_pending");

  // the sign-in screen comes first, then the approval page of the same link
  await signIn(page, `/cli-login?code=${start.user_code}`);
  await expect(page.getByRole("heading", { name: "Sign in a workstation" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(start.user_code)).toBeVisible();
  await expect(page.getByText("E2E-WORKSTATION")).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("heading", { name: "Workstation signed in" })).toBeVisible();

  const granted = await (await poll()).json();
  expect(granted.access_token).toMatch(/^hlt_/);
  expect(new Date(granted.expires_at).getTime()).toBeGreaterThan(Date.now() + 29 * 86400_000);
  expect((await (await poll()).json()).error).toBe("expired_token");
  const cfg = await request.get("/workstation/config", { headers: { Authorization: `Bearer ${granted.access_token}` } });
  expect(cfg.ok()).toBe(true);
  await request.delete(`/auth/tokens/${granted.token_id}`, { headers: { Authorization: `Bearer ${granted.access_token}` } });
});

test("a refused or unknown code signs nothing in", async ({ page, request }) => {
  const start = await (await request.post("/auth/cli/start", { data: { hostname: "E2E-DENIED" } })).json();
  await signIn(page, `/cli-login?code=${start.user_code}`);
  await page.getByRole("button", { name: "Deny" }).click();
  await expect(page.getByRole("heading", { name: "Sign-in refused" })).toBeVisible({ timeout: 30_000 });
  expect((await (await request.post("/auth/cli/token", { data: { device_code: start.device_code } })).json()).error).toBe("access_denied");

  await page.goto("/cli-login?code=BBBB-CCCC");
  await expect(page.getByRole("alert")).toContainText("Unknown or expired code");
  // without a code, the page asks for it
  await page.goto("/cli-login");
  await expect(page.getByRole("textbox", { name: "Code" })).toBeVisible();
});
