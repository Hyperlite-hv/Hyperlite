import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// A running VM whose saved definition differs from the running one: the header says what waits for its next start,
// on every tab. The VM is served by the test; the comparison is covered by tests/test_vm_pending.py.
const NAME = "e2e-pending-web";
const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const vm = { nom: NAME, etat: "actif", id: 4, uuid: NAME, vcpu: 2, memoire_mo: 2048, ip: null, utilisateur_ssh: null, uptime_s: 60, os: "Debian", stockage_zfs: false, stockage_iscsi: false, agent_invite: null, firmware: "bios" };

test("pending changes: listed under a running VM's header on every tab", async ({ page }) => {
  let changes = [
    { cle: "vcpu", objet: null, actuel: "2", prochain: "4" },
    { cle: "interface", objet: "52:54:00:00:00:02", actuel: null, prochain: "default · e1000" },
  ];
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/pending-changes(\\?.*)?$`), (r) => (api(r) ? r.fulfill(json({ en_marche: true, changements: changes })) : r.fallback()));

  await page.goto(`/vm/${NAME}?tab=summary`);
  const banner = page.getByRole("status").filter({ hasText: "Waiting for the VM's next start" });
  await expect(banner).toContainText("vCPU, Network card 52:54:00:00:00:02");
  await banner.getByRole("button", { name: "Show" }).click();
  await expect(banner.getByRole("row", { name: /vCPU 2 4/ })).toBeVisible();
  await expect(banner.getByRole("row", { name: /Network card 52:54:00:00:00:02 not present default · e1000/ })).toBeVisible();

  changes = [];
  await page.getByRole("tab", { name: "Tasks", exact: true }).click();
  await expect(banner).toHaveCount(0);
});
