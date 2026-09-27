import { existsSync, readdirSync } from "node:fs";
import { apiLogin, expect, goTo, PREFIX, test, uiLogin } from "../support/fixtures";
import type { APIRequestContext, Page } from "@playwright/test";

const stamp = Date.now().toString().slice(-6);
const SRC = `${PREFIX}src-${stamp}`;
const CLONE = `${PREFIX}clone-${stamp}`;
const TEMPLATE = `${PREFIX}tpl-${stamp}`;
const DEPLOYED = `${PREFIX}dep-${stamp}`;
const RESTORED = `${PREFIX}rst-${stamp}`;
const ALL = [SRC, CLONE, DEPLOYED, RESTORED];
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });

test.describe.configure({ mode: "serial", timeout: 240_000 });

async function exists(request: APIRequestContext, name: string) {
  return (await request.get(`/vms/${name}`, { headers: auth() })).ok();
}
async function selectVm(page: Page, name: string, tab = "") {
  await page.goto(`/vm/${name}${tab ? `?tab=${tab}` : ""}`);
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible({ timeout: 30_000 });
}
// Every VM operation outside the power buttons lives in the header Actions menu.
async function vmAction(page: Page, item: RegExp) {
  await page.locator(".nx-oh-acts").getByRole("button", { name: /^Actions/ }).click();
  await page.getByRole("menuitem", { name: item }).click();
}

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
  const res = await request.post("/vms", { headers: auth(), data: { name: SRC, vcpu: 1, memory_mb: 256, disks: [{ size_gb: 3 }], network: "hyperlite-isolated", username: "tester", password: "Testpass1" } });
  expect(res.status()).toBe(201);
});

test.afterAll(async ({ request }) => {
  for (const name of ALL) {
    if (await exists(request, name)) {
      await request.post(`/vms/${name}/stop?force=true`, { headers: auth() });
      await request.delete(`/vms/${name}?confirm=true`, { headers: auth() });
    }
  }
  await request.delete(`/templates/${TEMPLATE}?confirm=true`, { headers: auth() });
  const exportsRes = await request.get("/vm-exports", { headers: auth() });
  if (exportsRes.ok()) for (const f of (await exportsRes.json()) as Array<{ nom: string }>) if (f.nom.startsWith(PREFIX)) await request.delete(`/vm-exports/${encodeURIComponent(f.nom)}`, { headers: auth() });
  const backups = await request.get("/backups", { headers: auth() });
  if (backups.ok()) for (const b of (await backups.json()) as Array<{ id: number; vm_name: string }>) if (ALL.includes(b.vm_name)) await request.delete(`/backups/${b.id}?confirm=true`, { headers: auth() });
});

test.describe("Advanced VM operations (real backend)", () => {
  test("cloning a VM creates an independent VM after a name dialog", async ({ page, request }) => {
    await uiLogin(page);
    await selectVm(page, SRC);
    await vmAction(page, /^Clone/);
    const nameDialog = page.getByRole("dialog", { name: /Clone/ });
    await nameDialog.getByRole("textbox", { name: "Name of the copy" }).fill(CLONE);
    await nameDialog.getByRole("button", { name: "Clone", exact: true }).click();
    await expect.poll(() => exists(request, CLONE), { timeout: 120_000 }).toBe(true);
    await goTo(page, "Virtual Machines");
    await expect(page.getByRole("main").getByText(CLONE).first()).toBeVisible({ timeout: 60_000 });
    expect(existsSync(`/var/lib/libvirt/images/${CLONE}.qcow2`)).toBe(true);
  });

  test("cancelling the clone dialog creates nothing", async ({ page, request }) => {
    await uiLogin(page);
    await selectVm(page, SRC);
    await vmAction(page, /^Clone/);
    await page.getByRole("dialog", { name: /Clone/ }).getByRole("button", { name: "Cancel" }).click();
    await page.waitForLoadState("networkidle");
    const vms = (await (await request.get("/vms", { headers: auth() })).json()) as Array<{ nom: string }>;
    expect(vms.filter((v) => v.nom.startsWith(`${PREFIX}clone-`)).map((v) => v.nom)).toEqual([CLONE]);
  });

  test("a VM converted to a template can be deployed as a new VM", async ({ page, request }) => {
    await uiLogin(page);
    await selectVm(page, CLONE);
    await vmAction(page, /^Convert to template/);
    const tplDialog = page.getByRole("dialog", { name: /template/ });
    await tplDialog.getByRole("textbox", { name: "Template name" }).fill(TEMPLATE);
    await tplDialog.getByRole("button", { name: "Convert" }).click();
    await expect.poll(async () => (await request.get("/templates", { headers: auth() })).ok() && ((await (await request.get("/templates", { headers: auth() })).json()) as Array<{ nom: string }>).some((t) => t.nom === TEMPLATE), { timeout: 60_000 }).toBe(true);
    expect(await exists(request, CLONE), "the source VM is consumed by the conversion").toBe(false);
    // The screen leaves the consumed VM on its own; reloading before that lands on /vm/<gone VM>.
    await expect(page).toHaveURL(/\/datacenter/);

    await page.goto("/datacenter?tab=templates");
    await expect(page.getByRole("main").getByRole("tab", { name: /^Templates/ })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("main").getByText(TEMPLATE)).toBeVisible();
    await page.getByRole("button", { name: `Deploy template ${TEMPLATE}` }).click();
    const dialog = page.getByRole("dialog", { name: `Deploy ${TEMPLATE}` });
    await dialog.getByRole("textbox", { name: "Name of the new VM" }).fill(DEPLOYED);
    await dialog.getByRole("button", { name: "Deploy", exact: true }).click();
    await expect.poll(() => exists(request, DEPLOYED), { timeout: 120_000 }).toBe(true);
  });

  test("a cold backup can be created and then restored to a new VM (restore is verified, not assumed)", async ({ page, request }) => {
    await uiLogin(page);
    await selectVm(page, SRC, "backup");
    await page.getByRole("main").getByRole("button", { name: /Back up now/ }).click();
    await expect.poll(async () => ((await (await request.get(`/vms/${SRC}/backups`, { headers: auth() })).json()) as Array<{ statut: string }>).map((b) => b.statut), { timeout: 180_000 }).toContain("termine");
    await page.reload();
    await page.getByRole("main").getByRole("button", { name: /to a new VM$/ }).first().click();
    const restoreDialog = page.getByRole("dialog", { name: /Restore backup/ });
    await restoreDialog.getByRole("textbox", { name: "Name of the new VM" }).fill(RESTORED);
    await restoreDialog.getByRole("button", { name: "Restore" }).click();
    await expect.poll(() => exists(request, RESTORED), { timeout: 180_000 }).toBe(true);
    expect(existsSync(`/var/lib/libvirt/images/${RESTORED}.qcow2`)).toBe(true);
    const start = await request.post(`/vms/${RESTORED}/start`, { headers: auth() });
    expect(start.ok(), "the restored VM can be started: " + (await start.text())).toBe(true);
    await expect.poll(async () => ((await (await request.get(`/vms/${RESTORED}`, { headers: auth() })).json()) as { etat: string }).etat, { timeout: 60_000 }).toBe("actif");
  });

  test("a disk export is produced, listed, and downloadable with a one-time ticket", async ({ page, request }) => {
    const res = await request.post(`/vms/${SRC}/export`, { headers: auth() });
    expect(res.ok()).toBe(true);
    await uiLogin(page);
    await goTo(page, "Exports");
    await expect.poll(async () => ((await (await request.get("/vm-exports", { headers: auth() })).json()) as Array<{ nom: string }>).map((e) => e.nom).some((n) => n.startsWith(SRC)), { timeout: 120_000 }).toBe(true);
    await page.reload();
    await expect(page.getByRole("main").getByText(new RegExp(SRC)).first()).toBeVisible();
    const files = (await (await request.get("/vm-exports", { headers: auth() })).json()) as Array<{ nom: string }>;
    const file = files.find((f) => f.nom.startsWith(SRC))!.nom;
    const ticket = (await (await request.post(`/vm-exports/${encodeURIComponent(file)}/download-ticket`, { headers: auth() })).json()) as { ticket: string };
    const url = `/vm-exports/download?ticket=${encodeURIComponent(ticket.ticket)}`;
    const first = await request.get(url, { headers: { Range: "bytes=0-1023" } });
    expect(first.ok()).toBe(true);
    expect((await first.body()).length).toBe(1024);
    const second = await request.get(url);
    expect(second.status(), "a ticket can only be used once").toBe(401);
    await request.delete(`/vm-exports/${encodeURIComponent(file)}`, { headers: auth() });
  });

  test("the activity list shows the operations performed above with their real status", async ({ page }) => {
    await uiLogin(page);
    await goTo(page, "Tasks");
    const list = page.getByRole("main").getByRole("table");
    await expect(list).toContainText(/Create VM|Clone VM|Restore backup/);
    await expect(list.getByText(/^Done$/).first()).toBeVisible();
    await page.getByRole("group", { name: "Task filters" }).getByLabel("Status").selectOption({ label: "Failed" });
    await expect(list.getByText(/^Done$/)).toHaveCount(0);
  });

  test("leftover disks of this run are removed after cleanup", async ({ request }) => {
    for (const name of ALL) {
      await request.post(`/vms/${name}/stop?force=true`, { headers: auth() });
      await request.delete(`/vms/${name}?confirm=true`, { headers: auth() });
    }
    await request.delete(`/templates/${TEMPLATE}`, { headers: auth() });
    const leftovers = readdirSync("/var/lib/libvirt/images").filter((f) => f.startsWith(`${PREFIX}`) && ALL.some((n) => f.startsWith(n)));
    expect(leftovers).toEqual([]);
  });
});
