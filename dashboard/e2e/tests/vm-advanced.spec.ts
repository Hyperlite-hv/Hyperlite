import { existsSync, readdirSync } from "node:fs";
import { apiLogin, expect, PREFIX, test, uiLogin } from "../support/fixtures";
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
async function selectVm(page: Page, name: string) {
  await page.getByRole("treeitem", { name: new RegExp(name) }).click();
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
  await request.delete(`/templates/${TEMPLATE}`, { headers: auth() });
  const backups = await request.get("/backups", { headers: auth() });
  if (backups.ok()) for (const b of (await backups.json()) as Array<{ id: number; vm_name: string }>) if (ALL.includes(b.vm_name)) await request.delete(`/backups/${b.id}?confirm=true`, { headers: auth() });
});

test.describe("Advanced VM operations (real backend)", () => {
  test("cloning a VM creates an independent VM after a name prompt", async ({ page, request }) => {
    await uiLogin(page);
    await selectVm(page, SRC);
    page.once("dialog", (d) => {
      expect(d.type()).toBe("prompt");
      void d.accept(CLONE);
    });
    await page.getByRole("button", { name: "Clone", exact: true }).click();
    await expect.poll(() => exists(request, CLONE), { timeout: 120_000 }).toBe(true);
    await expect(page.getByRole("treeitem", { name: new RegExp(CLONE) })).toBeVisible({ timeout: 60_000 });
    expect(existsSync(`/var/lib/libvirt/images/${CLONE}.qcow2`)).toBe(true);
  });

  test("cancelling the clone prompt creates nothing", async ({ page, request }) => {
    await uiLogin(page);
    await selectVm(page, SRC);
    page.once("dialog", (d) => void d.dismiss());
    await page.getByRole("button", { name: "Clone", exact: true }).click();
    await page.waitForLoadState("networkidle");
    const vms = (await (await request.get("/vms", { headers: auth() })).json()) as Array<{ nom: string }>;
    expect(vms.filter((v) => v.nom.startsWith(`${PREFIX}clone-`)).map((v) => v.nom)).toEqual([CLONE]);
  });

  test("a VM converted to a template can be deployed as a new VM", async ({ page, request }) => {
    await uiLogin(page);
    await selectVm(page, CLONE);
    page.once("dialog", (d) => void d.accept(TEMPLATE));
    await page.getByRole("button", { name: "To template" }).click();
    await expect.poll(async () => (await request.get("/templates", { headers: auth() })).ok() && ((await (await request.get("/templates", { headers: auth() })).json()) as Array<{ nom: string }>).some((t) => t.nom === TEMPLATE), { timeout: 60_000 }).toBe(true);
    expect(await exists(request, CLONE), "the source VM is consumed by the conversion").toBe(false);

    await page.reload();
    await page.getByRole("tab", { name: "Templates", exact: true }).click();
    await expect(page.getByText(TEMPLATE)).toBeVisible();
    await page.getByRole("button", { name: `Deploy template ${TEMPLATE}` }).click();
    const dialog = page.getByRole("dialog", { name: `Deploy ${TEMPLATE}` });
    await dialog.getByRole("textbox", { name: "Name of the new VM" }).fill(DEPLOYED);
    await dialog.getByRole("button", { name: "Deploy", exact: true }).click();
    await expect.poll(() => exists(request, DEPLOYED), { timeout: 120_000 }).toBe(true);
  });

  test("a cold backup can be created and then restored to a new VM (restore is verified, not assumed)", async ({ page, request }) => {
    await uiLogin(page);
    await selectVm(page, SRC);
    await page.getByRole("tab", { name: "Backup", exact: true }).click();
    await page.getByRole("button", { name: /Back up now/ }).click();
    await expect.poll(async () => ((await (await request.get(`/vms/${SRC}/backups`, { headers: auth() })).json()) as Array<{ statut: string }>).map((b) => b.statut), { timeout: 180_000 }).toContain("termine");
    await page.reload();
    await selectVm(page, SRC);
    await page.getByRole("tab", { name: "Backup", exact: true }).click();
    page.once("dialog", (d) => {
      expect(d.type()).toBe("prompt");
      void d.accept(RESTORED);
    });
    await page.getByRole("button", { name: "New VM" }).first().click();
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
    await page.getByRole("tab", { name: "Exports", exact: true }).click();
    await expect.poll(async () => ((await (await request.get("/vm-exports", { headers: auth() })).json()) as Array<{ nom: string }>).map((e) => e.nom).some((n) => n.startsWith(SRC)), { timeout: 120_000 }).toBe(true);
    await page.reload();
    await page.getByRole("tab", { name: "Exports", exact: true }).click();
    await expect(page.getByText(new RegExp(SRC)).first()).toBeVisible();
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
    await page.getByRole("tab", { name: "Recent activity", exact: true }).click();
    const list = page.getByRole("region", { name: "Recent activity" });
    await expect(list).toContainText(/Create VM|Clone VM|Restore backup/);
    await page.getByRole("combobox", { name: "Filter by status" }).selectOption({ label: "Failed" });
    await expect(list.getByText(/^Completed$/)).toHaveCount(0);
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
