import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// Cloud-init after creation, under a VM's Hardware tab. The VM is served by the test; the drive itself is rebuilt
// and checked by tests/test_cloudinit_edit.py.
const NAME = "e2e-ci-web";
const KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGj0bXkPZ1d5bDx2eH1 ana@laptop";
const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const vm = { nom: NAME, etat: "actif", id: 1, uuid: NAME, vcpu: 1, memoire_mo: 512, ip: null, utilisateur_ssh: "debian", uptime_s: 60, os: "Debian", stockage_zfs: false, stockage_iscsi: false, agent_invite: null, firmware: "bios" };

test("cloud-init: keys and password validated, saved for the next boot, password never shown back", async ({ page }) => {
  let state = { disponible: true, en_marche: true, utilisateur: "debian", cles_ssh: [] as string[], modifie_le: null as string | null };
  const saved: unknown[] = [];
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/cloud-init$`), (r) => {
    if (!api(r)) return r.fallback();
    if (r.request().method() === "PUT") {
      const body = r.request().postDataJSON();
      saved.push(body);
      state = { ...state, utilisateur: body.utilisateur, cles_ssh: body.cles_ssh, modifie_le: new Date().toISOString() };
    }
    return r.fulfill(json({ ...state, recharge_a_chaud: true }));
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
  expect(saved).toEqual([{ utilisateur: "debian", cles_ssh: [KEY], mot_de_passe: "correct horse battery" }]);
  await expect(card.getByLabel("New password")).toHaveValue(""); // never kept in the page after saving
  await expect(card.getByText(/last changed/)).toBeVisible();
});

test("cloud-init: a VM installed from an ISO says why there is nothing to edit", async ({ page }) => {
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/cloud-init$`), (r) => (api(r) ? r.fulfill(json({ disponible: false, en_marche: true, utilisateur: null, cles_ssh: [], modifie_le: null })) : r.fallback()));
  await page.goto(`/vm/${NAME}?tab=cloudinit`);
  await expect(page.getByText(/installed from an ISO image, not made from a cloud image/)).toBeVisible();
});
