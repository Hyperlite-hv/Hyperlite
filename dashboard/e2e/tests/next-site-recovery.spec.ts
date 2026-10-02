import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import { ADMIN, expect, test } from "../support/fixtures";

// Recovery of another site on the Backups page. The other site's backups are written by the test into a directory
// the real backend reads (the backend runs on this machine); the backend really scans it. The restore itself
// (qemu-img, a new libvirt domain) is covered by tests/test_site_recovery.py, so the start request is captured here.
// The directory is under the backend's own backup directory: the scan reads only storage the node knows.
const BACKUPS = fileURLToPath(new URL("../../../data/backups", import.meta.url));
test.describe.configure({ mode: "serial", timeout: 90_000 });

let share = "";

function backup(vm: string, stamp: string, day: string) {
  const dir = join(share, vm, stamp);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "vda.qcow2"), "disk");
  writeFileSync(join(dir, "manifest.json"), JSON.stringify({
    version: 1, vm, source: "site-a", mode: "froid", cree_le: `2026-10-${day}T03:00:00+00:00`, firmware: "bios",
    fichiers: [{ nom: "vda.qcow2", role: "disque", cible: "vda", taille: 4, sha256: "x" }],
  }));
}

test.beforeAll(() => {
  mkdirSync(BACKUPS, { recursive: true });
  share = mkdtempSync(join(BACKUPS, "e2e-site-a-"));
  backup("e2e-site-web", "20261001T030000Z", "01");
  backup("e2e-site-web", "20261002T030000Z", "02");
  backup("e2e-site-db", "20261002T030000Z", "02");
});

test.afterAll(() => rmSync(share, { recursive: true, force: true }));

async function open(page: Page, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=backups");
}

test("the other site's backups are found and the chosen VMs are restored under their names", async ({ page }) => {
  let started: unknown = null;
  await page.route(/\/backups\/site-recovery$/, (route) => {
    started = route.request().postDataJSON();
    return route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ task_id: "t1" }) });
  });
  await open(page);
  const card = page.getByRole("region", { name: "Recovery of another site" });
  await card.getByLabel("Directory of the other site's backups").fill("/etc");
  await card.getByRole("button", { name: "Look for backups" }).click();
  await expect(card.getByText(/cannot be written in \/etc/)).toBeVisible({ timeout: 20_000 });

  await card.getByLabel("Directory of the other site's backups").fill(share);
  await card.getByRole("button", { name: "Look for backups" }).click();
  const web = card.getByRole("row", { name: /e2e-site-web/ });
  await expect(web).toContainText("site-a", { timeout: 20_000 });
  await expect(web).toContainText("1 older");

  // Two VMs under one name are refused before anything is sent.
  await card.getByLabel("Name of e2e-site-db on this site").fill("e2e-site-web");
  await expect(card.getByText("Two VMs have the same name.")).toBeVisible();
  await expect(card.getByRole("button", { name: "Restore 2 VM(s)" })).toBeDisabled();
  await card.getByLabel("Name of e2e-site-db on this site").fill("e2e-site-db-a");

  await card.getByLabel("Restore e2e-site-web").uncheck();
  await card.getByRole("button", { name: "Restore 1 VM(s)" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("e2e-site-db-a");
  await dialog.getByRole("button", { name: "Restore" }).click();
  await expect.poll(() => started).toEqual({
    elements: [{ chemin: join(share, "e2e-site-db", "20261002T030000Z"), nom: "e2e-site-db-a" }],
    reseau: null,
  });
});

test("in French, the card says what it does", async ({ page }) => {
  await open(page, "fr");
  const card = page.getByRole("region", { name: "Reprise d’un autre site" });
  await expect(card.getByText(/un site mort et un lien coupé se ressemblent/)).toBeVisible({ timeout: 20_000 });
  await card.getByLabel("Dossier des sauvegardes de l’autre site").fill(join(share, "nothing-here"));
  await card.getByRole("button", { name: "Chercher les sauvegardes" }).click();
  await expect(card.getByText(/n.est pas un dossier|is not a directory/)).toBeVisible({ timeout: 20_000 });
});
