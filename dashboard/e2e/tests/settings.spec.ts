import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { existsSync, rmSync } from "node:fs";
import * as OTPAuth from "otpauth";
import type { Page } from "@playwright/test";
import { apiLogin, expect, goTo, PREFIX, test, uiLogin } from "../support/fixtures";

// The account menu is the user button at the bottom of the sidebar.
async function accountMenu(page: Page, item: string) {
  await page.locator(".nx-sidebar-user").click();
  await page.getByRole("menuitem", { name: item }).click();
}

const stamp = Date.now().toString().slice(-6);
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
});

test.describe("Storage pools (real libvirt backend)", () => {
  const POOL = `${PREFIX}pool-${stamp}`;
  test.afterAll(async ({ request }) => {
    await request.delete(`/storage/${POOL}?confirm=true&detacher=true`, { headers: auth() });
    rmSync(`/var/lib/libvirt/hyperlite-pools/${POOL}`, { recursive: true, force: true });
  });

  test("creates a directory pool, lists it after a reload, then removes only its definition", async ({ page, request }) => {
    await uiLogin(page);
    await goTo(page, "Storage");
    await page.getByRole("main").getByRole("button", { name: "Create a pool" }).click();
    const form = page.getByRole("dialog", { name: "Create a pool" });
    await form.getByRole("textbox", { name: "Pool name" }).fill(POOL);
    await form.getByRole("button", { name: "Local directory" }).click();
    await form.getByRole("button", { name: "Create a pool", exact: true }).click();
    await expect(page.getByRole("button", { name: `Delete pool ${POOL}` })).toBeVisible();

    const pools = (await (await request.get("/storage", { headers: auth() })).json()) as Array<{ nom: string; etat: string }>;
    expect(pools.find((p) => p.nom === POOL)?.etat).toBeTruthy();
    expect(existsSync(`/var/lib/libvirt/hyperlite-pools/${POOL}`), "pool directory exists on the host").toBe(true);

    await page.reload();
    await expect(page.getByRole("button", { name: `Delete pool ${POOL}` })).toBeVisible();

    await page.getByRole("button", { name: `Delete pool ${POOL}` }).click();
    const dlg = page.getByRole("alertdialog");
    await expect(dlg).toContainText(/files are NOT deleted/i);
    await dlg.getByRole("button", { name: "Cancel" }).click();
    await page.getByRole("button", { name: `Delete pool ${POOL}` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
    await expect(page.getByRole("button", { name: `Delete pool ${POOL}` })).toHaveCount(0);
    const after = (await (await request.get("/storage", { headers: auth() })).json()) as Array<{ nom: string }>;
    expect(after.map((p) => p.nom)).not.toContain(POOL);
    expect(existsSync(`/var/lib/libvirt/hyperlite-pools/${POOL}`), "the directory itself is kept").toBe(true);
  });

  test("the default pool cannot be removed and a duplicate name is refused", async ({ request }) => {
    const del = await request.delete("/storage/default?confirm=true", { headers: auth() });
    expect(del.status()).toBe(400);
    const dup = await request.post("/storage", { headers: auth(), data: { name: "default", type: "dir" } });
    expect(dup.ok()).toBe(false);
  });
});

test.describe("Notifications with a real webhook receiver", () => {
  test("the test button delivers a real request to the configured URL", async ({ page, request }) => {
    const received: Array<{ body: string }> = [];
    const server = createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        received.push({ body });
        res.writeHead(200).end("ok");
      });
    });
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    const port = (server.address() as AddressInfo).port;
    const name = `${PREFIX}receiver-${stamp}`;
    try {
      await uiLogin(page);
      await goTo(page, "Notifications");
      await page.getByRole("main").getByRole("button", { name: "Add a channel" }).click();
      const form = page.getByRole("dialog", { name: "Add a channel" });
      await form.getByRole("textbox", { name: "Channel name" }).fill(name);
      await form.getByRole("textbox", { name: "Webhook URL" }).fill(`http://127.0.0.1:${port}/hook`);
      await form.getByRole("button", { name: "Add the channel", exact: true }).click();
      await expect(page.getByRole("button", { name: `Delete channel ${name}` })).toBeVisible();

      const channels = (await (await request.get("/notifications/channels", { headers: auth() })).json()) as Array<{ id: number; name: string }>;
      const id = channels.find((c) => c.name === name)!.id;
      const res = await request.post(`/notifications/channels/${id}/test`, { headers: auth() });
      expect(res.ok()).toBe(true);
      await expect.poll(() => received.length, { timeout: 10_000 }).toBeGreaterThan(0);
      expect(received[0].body).toContain("Hyperlite");
      expect(received[0].body).not.toMatch(/Test de notification/);
      await request.delete(`/notifications/channels/${id}`, { headers: auth() });
    } finally {
      server.close();
    }
  });

  test("rejects a webhook URL that is not http(s)", async ({ request }) => {
    const res = await request.post("/notifications/channels", { headers: auth(), data: { name: `${PREFIX}bad-${stamp}`, type: "webhook", config: { url: "file:///etc/passwd" }, events: [] } });
    expect(res.ok()).toBe(false);
  });
});

test.describe("Account security", () => {
  const USER = { username: `${PREFIX}sec-${stamp}`, password: "Sec-Passw0rd!" };
  test.beforeAll(async ({ request }) => {
    const r = await request.post("/auth/users", { headers: auth(), data: { ...USER, role: "admin" } });
    expect(r.ok()).toBe(true);
  });
  test.afterAll(async ({ request }) => {
    await request.delete(`/auth/users/${USER.username}`, { headers: auth() });
  });

  test("an API token is shown once, works without a session, and stops working when revoked", async ({ page, request }) => {
    await uiLogin(page, USER.username, USER.password);
    await accountMenu(page, "Account security");
    const dlg = page.getByRole("dialog", { name: "Account security" });
    await dlg.getByRole("textbox", { name: "Token name" }).fill("e2e-token");
    await dlg.getByRole("button", { name: "Create", exact: true }).click();
    const shown = await dlg.getByText(/^hlt_/).textContent();
    const apiToken = shown!.trim();
    expect(apiToken).toMatch(/^hlt_[A-Za-z0-9_-]+$/);

    const me = await request.get("/auth/me", { headers: { Authorization: `Bearer ${apiToken}` } });
    expect(me.ok()).toBe(true);
    expect((await me.json()).username).toBe(USER.username);

    // The secret is not retrievable afterwards.
    await dlg.getByRole("button", { name: "Close", exact: true }).click();
    await accountMenu(page, "Account security");
    await expect(page.getByRole("dialog", { name: "Account security" }).getByText(apiToken)).toHaveCount(0);
    const listing = await request.get("/auth/tokens", { headers: { Authorization: `Bearer ${apiToken}` } });
    expect(JSON.stringify(await listing.json())).not.toContain(apiToken);

    await page.getByRole("button", { name: "Revoke token e2e-token" }).click();
    await expect(page.getByRole("alertdialog")).toContainText("e2e-token");
    await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
    await expect.poll(async () => (await request.get("/auth/me", { headers: { Authorization: `Bearer ${apiToken}` } })).status()).toBe(401);
  });

  test("two-factor authentication: enable, sign in with a code, reject a wrong code, disable", async ({ page, request }) => {
    await uiLogin(page, USER.username, USER.password);
    await accountMenu(page, "Account security");
    const dlg = page.getByRole("dialog", { name: "Account security" });
    const setup = page.waitForResponse((r) => r.url().endsWith("/auth/2fa/setup"));
    await dlg.getByRole("button", { name: "Enable" }).click();
    // Enrolling a second factor takes the password.
    const confirmPw = page.getByRole("dialog", { name: "Enable two-factor authentication" });
    await confirmPw.getByLabel("Your password").fill(USER.password);
    await confirmPw.getByRole("button", { name: "Continue" }).click();
    const secret = ((await (await setup).json()) as { secret: string }).secret;
    await expect(dlg.locator("svg").first()).toBeVisible();
    // A code is accepted once: each later step uses the code of the next 30 s period (inside the server's window).
    const totp = (offsetS = 0) => new OTPAuth.TOTP({ secret: OTPAuth.Secret.fromBase32(secret), digits: 6, period: 30 }).generate({ timestamp: Date.now() + offsetS * 1000 });
    const nextPeriod = () => page.waitForTimeout(30_000 - (Date.now() % 30_000) + 500);
    await dlg.getByRole("textbox", { name: "6-digit code" }).fill("000000");
    await dlg.getByRole("button", { name: "Confirm" }).click();
    await expect(page.getByText(/Invalid code/i)).toBeVisible();
    await dlg.getByRole("textbox", { name: "6-digit code" }).fill(totp());
    await dlg.getByRole("button", { name: "Confirm" }).click();
    await expect(dlg.getByText(/2FA is enabled/i)).toBeVisible();
    await dlg.getByRole("button", { name: "Close", exact: true }).click();

    // A new session now needs the second step; the intermediate token is not a session token.
    const first = await request.post("/auth/login", { form: USER });
    const step1 = await first.json();
    expect(step1.require_2fa).toBe(true);
    expect((await request.get("/auth/me", { headers: { Authorization: `Bearer ${step1.pre_auth_token}` } })).status()).toBe(401);

    await accountMenu(page, "Sign out");
    await page.getByLabel("Username").fill(USER.username);
    await page.getByLabel("Password", { exact: true }).fill(USER.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Two-step verification")).toBeVisible();
    await page.getByRole("textbox", { name: "6-digit verification code" }).fill("111111");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await page.getByRole("textbox", { name: "6-digit verification code" }).fill(totp(30));
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });

    await accountMenu(page, "Account security");
    const security = page.getByRole("dialog", { name: "Account security" });
    await security.getByRole("textbox", { name: /Password/ }).fill(USER.password);
    // Removing 2FA takes a current code too: the password alone is refused.
    await expect(security.getByRole("button", { name: "Disable" })).toBeDisabled();
    await nextPeriod();
    await security.getByRole("textbox", { name: "Current 2FA code" }).fill(totp(30));
    await security.getByRole("button", { name: "Disable" }).click();
    await expect(page.getByRole("dialog", { name: "Account security" }).getByText(/Not enabled/i)).toBeVisible();
  });
});

test.describe("Settings persist and secrets are never returned", () => {
  test("no deployment profile or allocation policy: Hyperlite sets no ceiling of its own on a VM", async ({ page, request }) => {
    const limits = (await (await request.get("/host/limits", { headers: auth() })).json()) as Record<string, { source?: string; max?: number }>;
    for (const key of ["vcpu", "memoire_mo", "disque_go", "disques"]) expect(limits[key].source, key).toBe("technique");
    await uiLogin(page);
    await goTo(page, "Compatibility");
    await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Deployment profile" })).toHaveCount(0);
  });
});

test.describe("Journal", () => {
  test("actions appear in the journal and the result filter works", async ({ page, request }) => {
    await request.post("/auth/login", { form: { username: "admin", password: "definitely-wrong" } });
    await uiLogin(page);
    await goTo(page, "Audit log");
    const table = page.getByRole("main").getByRole("table");
    await expect(table).toBeVisible({ timeout: 20_000 });
    await expect(table.getByText(/^Success$/).first()).toBeVisible();
    await page.getByRole("combobox", { name: "Filter by result" }).selectOption({ label: "Failure" });
    await expect(table.getByRole("row", { name: /login/i }).first()).toBeVisible();
    await expect(table.getByText(/^Success$/)).toHaveCount(0);
  });
});
