import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// Rebooting the node from its Actions menu: the host name must be typed back, a running VM is named and shut down
// first only when asked. The power endpoint is served by the test (the backend part: tests/test_host_system.py).
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const vm = { nom: "e2e-power-web", etat: "actif", id: 3, uuid: "e2e-power-web", vcpu: 1, memoire_mo: 512, ip: null, utilisateur_ssh: null, uptime_s: 60, os: "Debian", stockage_zfs: false, stockage_iscsi: false, agent_invite: null, firmware: "bios" };

test("node reboot: typed name, running VMs shut down first", async ({ page }) => {
  const sent: unknown[] = [];
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(/\/health$/, (r) => (api(r) ? r.fulfill(json({ hostname: "hv-e2e", status: "ok" })) : r.fallback()));
  await page.route(/\/host\/system\/power$/, (r) => {
    if (!api(r)) return r.fallback();
    sent.push(r.request().postDataJSON());
    return r.fulfill(json({ tache: "t1" }, 202));
  });

  await page.goto("/node/local");
  await page.getByRole("main").getByRole("button", { name: "Actions" }).click();
  await page.getByRole("menuitem", { name: "Reboot the node…" }).click();
  const dialog = page.getByRole("alertdialog", { name: /Reboot/ });
  await expect(dialog.getByText("Running on this node: e2e-power-web.")).toBeVisible();
  const go = dialog.getByRole("button", { name: "Reboot", exact: true });
  await dialog.getByLabel("Type the node's name (hv-e2e) to confirm").fill("hv-e2");
  await expect(go).toBeDisabled();
  await dialog.getByLabel("Type the node's name (hv-e2e) to confirm").fill("hv-e2e");
  await dialog.getByLabel(/Shut down its VMs and containers cleanly first/).check();
  await go.click();
  await expect(page.getByText("Reboot requested")).toBeVisible();
  await expect(page).toHaveURL(/tab=tasks/);
  expect(sent).toEqual([{ action: "reboot", confirmation: "hv-e2e", arreter_invites: true }]);
});
