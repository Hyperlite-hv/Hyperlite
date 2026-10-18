import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// Cloud-init after creation, under a VM's Hardware tab. The VM is served by the test; the drive itself is rebuilt
// and checked by tests/test_cloudinit_edit.py.
const NAME = "e2e-ci-web";
const KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGj0bXkPZ1d5bDx2eH1 ana@laptop";
const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const vm = { name: NAME, state: "active", id: 1, uuid: NAME, vcpu: 1, memory_mb: 512, ip: null, ssh_user: "debian", uptime_s: 60, os: "Debian", zfs_storage: false, iscsi_storage: false, agent_invite: null, firmware: "bios" };

test("cloud-init: keys and password validated, saved for the next boot, password never shown back", async ({ page }) => {
  let state = { available: true, running: true, user: "debian", ssh_keys: [] as string[], modified_at: null as string | null };
  const saved: unknown[] = [];
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/cloud-init$`), (r) => {
    if (!api(r)) return r.fallback();
    if (r.request().method() === "PUT") {
      const body = r.request().postDataJSON();
      saved.push(body);
      state = { ...state, user: body.user, ssh_keys: body.ssh_keys, modified_at: new Date().toISOString() };
    }
    return r.fulfill(json({ ...state, hot_reload: true }));
  });

  await page.goto(`/vm/${NAME}?tab=cloudinit`);
  const card = page.getByRole("main").getByRole("region", { name: "Cloud-init" });
  await expect(card.getByLabel("User")).toHaveValue("debian");
  const save = card.getByRole("button", { name: "Save for the next boot" });
  await expect(save).toBeDisabled();

  await card.getByLabel("SSH public keys").fill("not a key");
  await expect(card.getByText(/Not an SSH public key/)).toBeVisible();
  await expect(save).toBeDisabled();
  await card.getByLabel("SSH public keys").fill(KEY);
  await card.getByLabel("New password").fill("short");
  await expect(card.getByText("At least 8 characters.")).toBeVisible();
  await card.getByLabel("New password").fill("correct horse battery");
  await save.click();

  await expect(page.getByText("Applied at the VM's next boot (restart it to apply now).")).toBeVisible();
  expect(saved).toEqual([{ user: "debian", ssh_keys: [KEY], password: "correct horse battery" }]);
  await expect(card.getByLabel("New password")).toHaveValue(""); // never kept in the page after saving
  await expect(card.getByText(/last changed/)).toBeVisible();
});

test("cloud-init: a VM installed from an ISO says why there is nothing to edit", async ({ page }) => {
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/cloud-init$`), (r) => (api(r) ? r.fulfill(json({ available: false, running: true, user: null, ssh_keys: [], modified_at: null })) : r.fallback()));
  await page.goto(`/vm/${NAME}?tab=cloudinit`);
  await expect(page.getByText(/installed from an ISO image, not made from a cloud image/)).toBeVisible();
});
