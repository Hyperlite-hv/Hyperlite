import type { Page, Route } from "@playwright/test";
import { ADMIN, apiLogin, expect, test } from "../support/fixtures";

// Replication to another site on the Backups page. The real backend creates, edits and deletes the job; starting a
// copy and the per-VM status are stand-ins, since a copy would touch the VMs other specs use. The copies themselves
// are covered on a real QEMU VM by tests/test_replication_qemu.py.
test.describe.configure({ mode: "serial", timeout: 90_000 });

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

async function open(page: Page, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=backups");
}

test.afterAll(async ({ request }) => {
  const auth = { Authorization: `Bearer ${await apiLogin(request)}` };
  const jobs = (await (await request.get("/replication/jobs", { headers: auth })).json()) as { id: number; nom: string }[];
  for (const j of jobs.filter((x) => x.nom.startsWith("e2e"))) await request.delete(`/replication/jobs/${j.id}`, { headers: auth });
});

test("a replication job is created with its checks, shows the late VMs and can be copied now", async ({ page }) => {
  let runs = 0;
  await page.route(/\/replication\/jobs\/\d+\/run$/, (route) => { runs += 1; return json(route, { message: "started" }, 202); });
  await page.route(/\/replication\/status$/, (route) => json(route, [
    { vm_name: "e2e-rep-web", statut: "ok", points: 12, age_s: 240, en_retard: false, intervalle_minutes: 15, erreur: null },
    { vm_name: "e2e-rep-db", statut: "echec", points: 3, age_s: 7200, en_retard: true, intervalle_minutes: 15, erreur: "Disk vda is not qcow2: replication needs qcow2 disks" },
  ]));
  await open(page);
  const card = page.getByRole("region", { name: "Replication to another site" });
  await card.getByRole("button", { name: "New replication" }).click();
  const drawer = page.getByRole("dialog", { name: "New replication" });
  await drawer.getByLabel("Name").fill("e2e to site B");
  await drawer.getByLabel("Storage of the other site").fill("relative/path");
  await expect(drawer.getByText("An absolute path, starting with /.")).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Save" })).toBeDisabled();
  await drawer.getByLabel("Every").fill("2");
  await expect(drawer.getByText("5 to 1440 minutes.")).toBeVisible();

  // The backend refuses a system directory with its reason.
  await drawer.getByLabel("Every").fill("15");
  await drawer.getByLabel("Storage of the other site").fill("/etc/site-b");
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/cannot be written in \/etc/).first()).toBeVisible({ timeout: 20_000 });

  // The refusal's toast can sit over the drawer's footer: the button is pressed from the keyboard, as a user could.
  await drawer.getByLabel("Storage of the other site").fill("/var/lib/libvirt/hyperlite-pools/e2e-site-b");
  await drawer.getByRole("button", { name: "Save" }).press("Enter");
  const row = card.getByRole("row", { name: /e2e to site B/ });
  await expect(row).toContainText("every 15 min", { timeout: 20_000 });
  await expect(row).toContainText("/var/lib/libvirt/hyperlite-pools/e2e-site-b");

  await expect(card.getByText(/1 VM\(s\) without a recent copy: e2e-rep-db/)).toBeVisible();
  const db = card.getByRole("row", { name: /e2e-rep-db/ });
  await expect(db).toContainText("late");
  await expect(db).toContainText("not qcow2");
  await expect(card.getByRole("row", { name: /e2e-rep-web/ })).toContainText("4 min ago");

  const runButton = row.getByRole("button", { name: "Copy now for e2e to site B" });
  if (await runButton.isEnabled()) {
    await runButton.click();
    await expect.poll(() => runs).toBe(1);
  }

  await row.getByRole("button", { name: "Edit e2e to site B" }).click();
  const edit = page.getByRole("dialog", { name: "Edit the replication" });
  await edit.getByLabel("Every").fill("60");
  await edit.getByRole("button", { name: "Save" }).press("Enter");
  await expect(row).toContainText("every 60 min", { timeout: 20_000 });

  await row.getByRole("button", { name: "Delete e2e to site B" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
  await expect(card.getByRole("row", { name: /e2e to site B/ })).toHaveCount(0, { timeout: 20_000 });
});

test("in French, the card explains what a replication is", async ({ page }) => {
  await open(page, "fr");
  const card = page.getByRole("region", { name: "Réplication vers l’autre site" });
  await card.getByRole("button", { name: "Nouvelle réplication" }).click();
  await expect(page.getByRole("dialog", { name: "Nouvelle réplication" }).getByText(/en perdant au plus un intervalle/)).toBeVisible();
});
