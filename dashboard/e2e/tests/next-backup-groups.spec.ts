import { apiLogin, expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// A grouped backup job on the Backups page, against the real backend: created for a tag with a GFS retention, one
// VM left out, then deleted. The VMs and tags are served by the test so the selection is known; running backups
// is covered by tests/test_backup_groups.py.
const NAME = `e2e-nightly-${Date.now().toString().slice(-6)}`;
const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const vm = (nom: string) => ({ nom, etat: "arrete", id: null, uuid: nom, vcpu: 1, memoire_mo: 512, ip: null, utilisateur_ssh: null, uptime_s: null, os: "Debian", stockage_zfs: false, stockage_iscsi: false, agent_invite: null, firmware: "bios" });

test("backup jobs: a tag's VMs on one schedule with GFS retention", async ({ page, request }) => {
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm("e2e-bg-web"), vm("e2e-bg-db")])) : r.fallback()));
  await page.route(/\/meta$/, (r) => (api(r) ? r.fulfill(json([{ kind: "vm", node: null, nom: "e2e-bg-web", tags: ["e2e-prod"], a_des_notes: false }])) : r.fallback()));

  await page.goto("/datacenter?tab=backups");
  const main = page.getByRole("main");
  await main.getByRole("button", { name: "Add a backup job" }).click();
  const drawer = page.getByRole("dialog", { name: "Add a backup job" });
  await drawer.getByLabel("Name").fill(NAME);
  await drawer.getByLabel("VMs", { exact: true }).selectOption("etiquette");
  await drawer.getByLabel("Tag").selectOption("e2e-prod");
  await drawer.getByRole("checkbox", { name: "e2e-bg-db" }).check();
  await drawer.getByLabel("Time", { exact: true }).fill("03:15");
  await drawer.getByLabel("Weekly").fill("0");
  await expect(drawer.getByText("1 to 260, or empty")).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Save" })).toBeDisabled();
  await drawer.getByLabel("Weekly").fill("4");
  await drawer.getByLabel("Monthly").fill("6");
  await drawer.getByLabel("Backup directory").fill("/etc/backups");
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Backups cannot be written in /etc/backups")).toBeVisible();
  await drawer.getByLabel("Backup directory").fill("");
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Backup job saved")).toBeVisible();

  const row = main.getByRole("row", { name: new RegExp(NAME) });
  await expect(row).toContainText("Tag e2e-prod");
  await expect(row).toContainText("Except e2e-bg-db");
  await expect(row).toContainText("7 last · 4 weekly · 6 monthly");
  await expect(row).toContainText("03:15");

  const token = await apiLogin(request);
  const jobs = await (await request.get("/backup-groups", { headers: { Authorization: `Bearer ${token}` } })).json();
  const job = jobs.find((j: { nom: string }) => j.nom === NAME);
  expect(job).toMatchObject({ selection: "etiquette", valeur: "e2e-prod", exclues: ["e2e-bg-db"], retention_count: 7, garder_jours: null, garder_semaines: 4, garder_mois: 6 });

  await row.getByRole("button", { name: `Delete ${NAME}` }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
  await expect(main.getByRole("row", { name: new RegExp(NAME) })).toHaveCount(0);
});
