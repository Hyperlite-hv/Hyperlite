import { existsSync, rmSync } from "node:fs";
import type { Page } from "@playwright/test";
import { apiLogin, expect, test, ADMIN, PREFIX } from "../support/fixtures";

// Rebuilt Storage and Network pages against the real libvirt backend.
const stamp = Date.now().toString().slice(-6);
const POOL = `${PREFIX}pool-${stamp}`;
const NET = `${PREFIX}net-${stamp}`;
const ISO = `${PREFIX}iso-${stamp}.iso`;
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });

test.describe.configure({ mode: "serial", timeout: 120_000 });

async function nextLogin(page: Page, tab: string) {
  await page.addInitScript(() => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "en"); } });
  await page.goto("/");
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto(`/datacenter?tab=${tab}`);
}

test.beforeAll(async ({ request }) => { token = await apiLogin(request); });
const OBS = { username: `${PREFIX}obs-${stamp}`, password: "Observer-Pass-1" };
test.afterAll(async ({ request }) => {
  await request.delete(`/auth/users/${OBS.username}`, { headers: auth() }).catch(() => {});
  await request.delete(`/storage/${POOL}?confirm=true&detacher=true`, { headers: auth() }).catch(() => {});
  rmSync(`/var/lib/libvirt/hyperlite-pools/${POOL}`, { recursive: true, force: true });
  await request.delete(`/networks/${NET}?confirm=true`, { headers: auth() }).catch(() => {});
  await request.delete(`/isos/${ISO}?confirm=true`, { headers: auth() }).catch(() => {});
});

test.describe("Storage page", () => {
  test("creates a directory pool, shows its usage and volumes, removes only its definition after a confirmation", async ({ page, request }) => {
    await nextLogin(page, "storage");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { level: 2, name: /^Storage pools/ })).toBeVisible();
    await expect(main.getByRole("meter").first()).toBeVisible();
    await main.getByRole("button", { name: "Create a pool" }).click();
    await main.getByRole("textbox", { name: "Pool name" }).fill(POOL);
    await main.getByRole("button", { name: "Create", exact: true }).click();
    await expect(main.getByRole("button", { name: `Delete pool ${POOL}` })).toBeVisible({ timeout: 20_000 });
    const pools = (await (await request.get("/storage", { headers: auth() })).json()) as { nom: string; etat: string }[];
    expect(pools.find((p) => p.nom === POOL)?.etat).toBeTruthy();
    expect(existsSync(`/var/lib/libvirt/hyperlite-pools/${POOL}`), "pool directory exists").toBe(true);
    // volumes of the default pool are listed on demand
    await main.getByRole("button", { name: "default", exact: true }).click();
    await expect(main.getByText(/Volumes are only listed|No volume in this pool|in use|Free/).first()).toBeVisible();
    // cancelling keeps the pool, confirming removes only the definition
    await main.getByRole("button", { name: `Delete pool ${POOL}` }).click();
    await expect(page.getByRole("alertdialog")).toContainText("Its files are NOT deleted");
    await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
    await expect(main.getByRole("button", { name: `Delete pool ${POOL}` })).toBeVisible();
    await main.getByRole("button", { name: `Delete pool ${POOL}` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
    await expect(main.getByRole("button", { name: `Delete pool ${POOL}` })).toHaveCount(0, { timeout: 20_000 });
    expect(existsSync(`/var/lib/libvirt/hyperlite-pools/${POOL}`), "the directory itself is kept").toBe(true);
  });

  test("the default pool has no delete button; ISO upload, list and confirmed deletion", async ({ page, request }) => {
    await nextLogin(page, "storage");
    const main = page.getByRole("main");
    await expect(main.getByRole("button", { name: "Delete pool default" })).toHaveCount(0);
    await page.getByLabel("ISO image file").setInputFiles({ name: ISO, mimeType: "application/octet-stream", buffer: Buffer.alloc(4096, 1) });
    await expect(main.getByRole("button", { name: `Delete ISO ${ISO}` })).toBeVisible({ timeout: 30_000 });
    await main.getByRole("button", { name: `Delete ISO ${ISO}` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
    await expect(main.getByRole("button", { name: `Delete ISO ${ISO}` })).toBeVisible();
    await main.getByRole("button", { name: `Delete ISO ${ISO}` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
    await expect(main.getByRole("button", { name: `Delete ISO ${ISO}` })).toHaveCount(0, { timeout: 20_000 });
    const isos = (await (await request.get("/isos", { headers: auth() })).json()) as { nom: string }[];
    expect(isos.map((i) => i.nom)).not.toContain(ISO);
  });
});

test.describe("Network page", () => {
  test("validates the form, creates an isolated network, shows details, deletes it after a confirmation", async ({ page, request }) => {
    await nextLogin(page, "reseau");
    const main = page.getByRole("main");
    await main.getByRole("button", { name: "Create a virtual network" }).click();
    // invalid input is explained, nothing is sent
    await main.getByRole("textbox", { name: "Gateway (e.g. 192.168.150.1)" }).fill("not-an-ip");
    await main.getByRole("button", { name: "Create", exact: true }).click();
    await expect(main.getByRole("alert").filter({ hasText: "name is required" })).toBeVisible();
    await expect(main.getByRole("alert").filter({ hasText: "valid IPv4" }).first()).toBeVisible();
    // bridge mode swaps the fields
    await main.getByRole("combobox", { name: "Network mode" }).selectOption({ label: "Bridge to an existing physical network" });
    await expect(main.getByRole("textbox", { name: "Host bridge name (e.g. br0)" })).toBeVisible();
    await main.getByRole("combobox", { name: "Network mode" }).selectOption({ label: "Isolated (no external access)" });
    // a valid isolated network
    await main.getByRole("textbox", { name: "Name", exact: true }).fill(NET);
    await main.getByRole("textbox", { name: "Gateway (e.g. 192.168.150.1)" }).fill("192.168.177.1");
    await main.getByRole("textbox", { name: "DHCP start" }).fill("192.168.177.10");
    await main.getByRole("textbox", { name: "DHCP end" }).fill("192.168.177.100");
    await main.getByRole("button", { name: "Create", exact: true }).click();
    await expect(main.getByRole("button", { name: `Delete network ${NET}` })).toBeVisible({ timeout: 30_000 });
    // details: subnet, leases, firewall
    await main.getByRole("button", { name: NET, exact: true }).click();
    await expect(main.getByText("Active DHCP leases")).toBeVisible();
    await expect(main.getByText("Network firewall")).toBeVisible();
    // protected networks cannot be deleted; this one can, after a confirmation
    await expect(main.getByRole("button", { name: "Delete network default" })).toHaveCount(0);
    await main.getByRole("button", { name: `Delete network ${NET}` }).click();
    await expect(page.getByRole("alertdialog")).toContainText("lose their connectivity");
    await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
    await expect(main.getByRole("button", { name: `Delete network ${NET}` })).toBeVisible();
    await main.getByRole("button", { name: `Delete network ${NET}` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
    await expect.poll(async () => {
      const r = await request.get("/networks", { headers: auth() });
      if (!r.ok()) return "retry"; // libvirt can answer 500 for a moment right after a deletion
      return ((await r.json()) as { nom: string }[]).some((n) => n.nom === NET) ? "present" : "gone";
    }, { timeout: 30_000 }).toBe("gone");
  });
});

test.describe("Journal, Backups, Exports and the administrator gate", () => {
  test("the journal sends every filter, including the end date, and can be exported", async ({ page }) => {
    await nextLogin(page, "journal");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { level: 2, name: /^Journal entries/ })).toBeVisible();
    await expect(main.getByRole("table")).toBeVisible({ timeout: 20_000 });
    const asked = page.waitForRequest((r) => r.url().includes("/audit?") && r.url().includes("jusqu_a="));
    await main.getByLabel("Show entries until").fill("2099-01-01T00:00");
    const req = await asked;
    expect(req.url()).toContain("limit=300");
    await main.getByLabel("Filter by result").selectOption("echec");
    await expect(main.getByRole("button", { name: "Export CSV" })).toBeVisible();
  });

  test("backups and exports show a helpful empty state or their table", async ({ page }) => {
    await nextLogin(page, "backups");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { level: 2, name: /^Backups/ })).toBeVisible();
    await expect(main.getByText(/No backups yet|Location \/ cause/)).toBeVisible({ timeout: 20_000 });
    await page.goto("/datacenter?tab=exports");
    await expect(main.getByRole("heading", { level: 2, name: /^Exports/ })).toBeVisible();
    await expect(main.getByText(/No exports yet|Download/).first()).toBeVisible({ timeout: 20_000 });
  });

  test("an observer gets an explanation, not spinners or 403 errors, on administrator pages", async ({ page, request }) => {
    const created = await request.post("/auth/users", { headers: auth(), data: { ...OBS, role: "observateur" } });
    expect(created.ok()).toBe(true);
    const failures: string[] = [];
    page.on("response", (r) => { if (r.status() === 403) failures.push(new URL(r.url()).pathname); });
    await page.addInitScript(() => { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "en"); });
    await page.goto("/");
    await page.getByLabel("Username").fill(OBS.username);
    await page.getByLabel("Password").fill(OBS.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
    for (const tab of ["permissions", "sso", "journal", "exports"]) {
      await page.goto(`/datacenter?tab=${tab}`);
      await expect(page.getByRole("main").getByRole("heading", { name: "Administrators only" }), tab).toBeVisible();
    }
    await page.waitForTimeout(1500);
    expect(failures, "no administrator endpoint was called").toEqual([]);
    // the observer sees no Create menu
    await expect(page.getByRole("button", { name: "Create", exact: true })).toHaveCount(0);
  });
});
