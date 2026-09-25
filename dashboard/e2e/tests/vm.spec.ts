import { existsSync } from "node:fs";
import { apiLogin, expect, PREFIX, test, uiLogin } from "../support/fixtures";

const stamp = Date.now().toString().slice(-6);
const NAME = `${PREFIX}vm-${stamp}`;
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });
const DISK = `/var/lib/libvirt/images/${NAME}.qcow2`;

test.describe.configure({ mode: "serial", timeout: 180_000 });

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
});

test.afterAll(async ({ request }) => {
  // Only the VM created by this file: force stop if needed, then delete.
  const res = await request.get(`/vms/${NAME}`, { headers: auth() });
  if (res.ok()) {
    await request.post(`/vms/${NAME}/stop?force=true`, { headers: auth() });
    await request.delete(`/vms/${NAME}?confirm=true`, { headers: auth() });
  }
});

const state = async (request: import("@playwright/test").APIRequestContext) => {
  const r = await request.get(`/vms/${NAME}`, { headers: auth() });
  return r.ok() ? ((await r.json()) as { etat: string }).etat : "missing";
};

test.describe("Virtual machine lifecycle (real libvirt/QEMU backend)", () => {
  test("the creation wizard validates its fields before allowing the next step", async ({ page }) => {
    await uiLogin(page);
    await page.getByRole("button", { name: "Create VM" }).click();
    const dlg = page.getByRole("dialog");
    await dlg.getByRole("button", { name: "Next" }).click();
    await dlg.getByRole("button", { name: "Next" }).click();
    await expect(dlg.getByRole("button", { name: "Next" })).toBeDisabled();
    await dlg.getByRole("textbox", { name: "VM name" }).fill(NAME);
    await expect(dlg.getByRole("button", { name: "Next" })).toBeDisabled();
    await dlg.getByRole("textbox", { name: "User" }).fill("tester");
    await dlg.getByRole("textbox", { name: "Password" }).fill("Testpass1");
    await expect(dlg.getByRole("button", { name: "Next" })).toBeEnabled();
    await dlg.getByRole("button", { name: "Close" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Discard" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(await state(page.request)).toBe("missing");
  });

  test("creates a VM through the wizard, follows the task and finds it after a reload", async ({ page, request, problems }) => {
    await uiLogin(page);
    await page.getByRole("button", { name: "Create VM" }).click();
    const dlg = page.getByRole("dialog");
    await dlg.getByRole("button", { name: "Next" }).click(); // node
    await dlg.getByRole("button", { name: "Next" }).click(); // template: Debian cloud image
    await dlg.getByRole("textbox", { name: "VM name" }).fill(NAME);
    await dlg.getByRole("spinbutton", { name: "Memory in MB" }).fill("256");
    await dlg.getByRole("spinbutton", { name: "Size of disk 1 in GB" }).fill("3");
    await dlg.getByRole("textbox", { name: "User" }).fill("tester");
    await dlg.getByRole("textbox", { name: "Password" }).fill("Testpass1");
    await dlg.getByRole("button", { name: "Next" }).click();
    await dlg.getByRole("radio", { name: /hyperlite-isolated/ }).check();
    await dlg.getByRole("button", { name: "Next" }).click();
    await expect(dlg).toContainText(NAME);
    await dlg.getByRole("button", { name: "Create the VM" }).click();

    await expect(page.getByRole("treeitem", { name: new RegExp(NAME) })).toBeVisible({ timeout: 90_000 });
    await expect.poll(() => state(request), { timeout: 60_000 }).toBe("arrete");
    expect(existsSync(DISK), "disk file exists on the host").toBe(true);

    await page.reload();
    await expect(page.getByRole("treeitem", { name: new RegExp(NAME) })).toBeVisible();
    expect(problems.filter((p) => !/Failed to load resource/.test(p))).toEqual([]);
  });

  test("refuses to create a second VM with the same name", async ({ request }) => {
    const res = await request.post("/vms", { headers: auth(), data: { name: NAME, vcpu: 1, memory_mb: 256, disks: [{ size_gb: 3 }], network: "hyperlite-isolated", username: "tester", password: "Testpass1" } });
    expect(res.status()).toBe(422);
    expect(JSON.stringify(await res.json())).toMatch(/already exists/);
  });

  test("rejects out-of-range resources with a precise message", async ({ request }) => {
    const res = await request.post("/vms", { headers: auth(), data: { name: `${PREFIX}big-${stamp}`, vcpu: 999, memory_mb: 99999999, disks: [{ size_gb: 3 }], network: "hyperlite-isolated", username: "tester", password: "Testpass1" } });
    expect(res.status()).toBe(422);
    expect(JSON.stringify(await res.json())).toMatch(/vCPU|Memory/i);
  });

  test("starts the VM, shows it running, and does not offer to start it again", async ({ page, request }) => {
    await uiLogin(page);
    await page.getByRole("treeitem", { name: new RegExp(NAME) }).click();
    await page.getByRole("button", { name: "Start", exact: true }).click();
    await expect.poll(() => state(request), { timeout: 60_000 }).toBe("actif");
    await expect(page.getByRole("button", { name: "Start", exact: true })).toBeDisabled();
    await expect(page.getByText("Running").first()).toBeVisible();
    await page.reload();
    await page.getByRole("treeitem", { name: new RegExp(NAME) }).click();
    await expect(page.getByRole("button", { name: "Start", exact: true })).toBeDisabled();
    // A second start through the API is refused (conflicting operation).
    const again = await request.post(`/vms/${NAME}/start`, { headers: auth() });
    expect(again.ok()).toBe(false);
  });

  test("opens the graphical console window for the running VM", async ({ page, context }) => {
    await uiLogin(page);
    await page.getByRole("treeitem", { name: new RegExp(NAME) }).click();
    await page.getByRole("tab", { name: "Console", exact: true }).click();
    const [popup] = await Promise.all([context.waitForEvent("page"), page.getByRole("button", { name: "Open in a new window" }).click()]);
    await expect(popup).toHaveURL(new RegExp(`/console/${NAME}`));
    await expect(popup.getByText(/Graphical console|Connecting|Disconnect/).first()).toBeVisible();
    await popup.close();
  });

  test("changing memory is refused while the VM runs, and the option is explained", async ({ page }) => {
    await uiLogin(page);
    await page.getByRole("treeitem", { name: new RegExp(NAME) }).click();
    await page.getByRole("tab", { name: "Options", exact: true }).click();
    await expect(page.getByRole("spinbutton", { name: "Memory in MB" })).toBeDisabled();
    await expect(page.getByText(/Stop the VM to change its resources/)).toBeVisible();
  });

  test("force-stops the VM after a confirmation", async ({ page, request }) => {
    await uiLogin(page);
    await page.getByRole("treeitem", { name: new RegExp(NAME) }).click();
    await page.getByRole("button", { name: "Force stop" }).click();
    const dlg = page.getByRole("alertdialog");
    await expect(dlg).toContainText(NAME);
    await dlg.getByRole("button", { name: "Cancel" }).click();
    expect(await state(request)).toBe("actif");
    await page.getByRole("button", { name: "Force stop" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Force stop" }).click();
    await expect.poll(() => state(request), { timeout: 30_000 }).toBe("arrete");
    // Stopping an already stopped VM is refused.
    const again = await request.post(`/vms/${NAME}/stop`, { headers: auth() });
    expect(again.status()).toBe(409);
  });

  test("edits the memory of the stopped VM and the change persists", async ({ page, request }) => {
    await uiLogin(page);
    await page.getByRole("treeitem", { name: new RegExp(NAME) }).click();
    await page.getByRole("tab", { name: "Options", exact: true }).click();
    await page.getByRole("spinbutton", { name: "Memory in MB" }).fill("384");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(async () => ((await (await request.get(`/vms/${NAME}`, { headers: auth() })).json()) as { memoire_mo: number }).memoire_mo).toBe(384);
    await page.reload();
    await page.getByRole("treeitem", { name: new RegExp(NAME) }).click();
    await page.getByRole("tab", { name: "Options", exact: true }).click();
    await expect(page.getByRole("spinbutton", { name: "Memory in MB" })).toHaveValue("384");
  });

  test("creates one snapshot even when the button is double-clicked, then deletes it", async ({ page, request }) => {
    const snapshots = async () => ((await (await request.get(`/vms/${NAME}/snapshots`, { headers: auth() })).json()) as Array<{ nom: string }>).map((s) => s.nom);
    await uiLogin(page);
    await page.getByRole("treeitem", { name: new RegExp(NAME) }).click();
    await page.getByRole("tab", { name: "Snapshots", exact: true }).click();
    await page.getByRole("button", { name: "Create a snapshot" }).dblclick();
    await expect.poll(async () => (await snapshots()).length, { timeout: 30_000 }).toBeGreaterThan(0);
    await page.waitForTimeout(3000); // let any duplicate request land before counting
    const names = await snapshots();
    expect(names, "a double click must not create two snapshots").toHaveLength(1);
    await expect(page.getByRole("button", { name: `Delete snapshot ${names[0]}` })).toBeVisible();
    await page.getByRole("button", { name: `Delete snapshot ${names[0]}` }).click();
    await expect(page.getByRole("alertdialog")).toContainText(names[0]);
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
    await expect.poll(async () => (await snapshots()).length, { timeout: 30_000 }).toBe(0);
  });

  test("deletes the VM after a confirmation and leaves no orphan disk", async ({ page, request }) => {
    await uiLogin(page);
    await page.getByRole("treeitem", { name: new RegExp(NAME) }).click();
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    const dlg = page.getByRole("alertdialog");
    await expect(dlg).toContainText(NAME);
    await dlg.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("treeitem", { name: new RegExp(NAME) })).toBeVisible();
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
    await expect(page.getByRole("treeitem", { name: new RegExp(NAME) })).toHaveCount(0);
    await expect.poll(() => state(request), { timeout: 30_000 }).toBe("missing");
    await expect.poll(() => existsSync(DISK), { timeout: 30_000, message: "disk file removed" }).toBe(false);
    await page.reload();
    await expect(page.getByRole("treeitem", { name: new RegExp(NAME) })).toHaveCount(0);
  });
});
