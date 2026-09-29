import { expect, goTo, test, uiLogin } from "../support/fixtures";
import type { Page, Route } from "@playwright/test";

// Bulk actions on the VM list. The VMs are served by the test (as in degraded.spec.ts): creating several real
// VMs would take minutes, and what is checked here is the selection, the confirmation and the per-VM calls.
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const vm = (nom: string, etat: string) => ({
  nom, etat, id: null, uuid: nom, vcpu: 1, memoire_mo: 512, ip: null, utilisateur_ssh: null, uptime_s: null, os: "Debian",
  stockage_zfs: false, stockage_iscsi: false, agent_invite: null, firmware: "bios",
});

async function serveVms(page: Page, initial: Record<string, string>, refuse: string[] = []) {
  const states = { ...initial };
  const calls: string[] = [];
  // API calls only: the page itself may be served at a /vms address.
  const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
  await page.route(/\/vms(\?.*)?$/, (r: Route) => (api(r) && r.request().method() === "GET" ? r.fulfill(json(Object.entries(states).map(([n, e]) => vm(n, e)))) : r.fallback()));
  await page.route(/\/vms\/([^/?]+)\/(start|stop|restart)(\?.*)?$/, (r: Route) => {
    if (!api(r)) return r.fallback();
    const [, name, action] = new URL(r.request().url()).pathname.match(/\/vms\/([^/]+)\/(\w+)$/) ?? [];
    const nom = decodeURIComponent(name);
    calls.push(`${action} ${nom}`);
    if (refuse.includes(nom)) return r.fulfill(json({ detail: `${nom} refused by the test` }, 409));
    states[nom] = action === "stop" ? "arrete" : "actif";
    return r.fulfill(json({ nom, etat: states[nom], ip: null }));
  });
  return { states, calls };
}

test("bulk start: the selection, a confirmation naming the VMs and the ones skipped, one call per VM", async ({ page }) => {
  await uiLogin(page);
  const backend = await serveVms(page, { "e2e-bulk-a": "arrete", "e2e-bulk-b": "arrete", "e2e-bulk-c": "actif" });
  await goTo(page, "Virtual Machines");
  const table = page.getByRole("table");
  await expect(table.getByRole("button", { name: "e2e-bulk-a", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Bulk actions" })).toHaveCount(0);

  await page.getByRole("checkbox", { name: /Select the 3 VM\(s\) shown/ }).check();
  const bar = page.getByRole("region", { name: "Bulk actions" });
  await expect(bar).toContainText("3 VM(s) selected");
  // Each button counts the VMs it applies to: two stopped VMs can start, one running VM can shut down.
  await expect(bar.getByRole("button", { name: /^Start 2$/ })).toBeVisible();
  await expect(bar.getByRole("button", { name: /^Stop 1$/ })).toBeVisible();

  await bar.getByRole("button", { name: /^Start 2$/ }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("e2e-bulk-a, e2e-bulk-b");
  await expect(dialog).toContainText("Left as they are (1): e2e-bulk-c");
  await dialog.getByRole("button", { name: "Start 2 VM(s)" }).click();

  await expect(page.getByText("Start: 2 of 2 VM(s) done")).toBeVisible();
  expect(backend.calls.sort()).toEqual(["start e2e-bulk-a", "start e2e-bulk-b"]);
  // A fully successful run clears the selection.
  await expect(bar).toHaveCount(0);
});

test("bulk stop: a refused VM is named, the others go on, and the selection stays for a retry", async ({ page }) => {
  await uiLogin(page);
  const backend = await serveVms(page, { "e2e-bulk-x": "actif", "e2e-bulk-y": "actif" }, ["e2e-bulk-y"]);
  await goTo(page, "Virtual Machines");
  await page.getByRole("checkbox", { name: "Select e2e-bulk-x" }).check();
  await page.getByRole("checkbox", { name: "Select e2e-bulk-y" }).check();
  const bar = page.getByRole("region", { name: "Bulk actions" });
  await bar.getByRole("button", { name: /^Stop 2$/ }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Stop 2 VM(s)" }).click();

  await expect(page.getByText("Stop failed for 1 VM(s)")).toBeVisible();
  await expect(page.getByText("e2e-bulk-y: e2e-bulk-y refused by the test")).toBeVisible();
  expect(backend.calls.sort()).toEqual(["stop e2e-bulk-x", "stop e2e-bulk-y"]);
  await expect(bar).toContainText("2 VM(s) selected");

  // Cancelling a confirmation sends nothing.
  await bar.getByRole("button", { name: /^Force stop 1$/ }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  expect(backend.calls).toHaveLength(2);
  await bar.getByRole("button", { name: "Clear the selection" }).click();
  await expect(bar).toHaveCount(0);
});
