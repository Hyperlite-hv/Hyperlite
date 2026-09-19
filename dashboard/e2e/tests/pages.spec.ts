import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, test, uiLogin } from "../support/fixtures";

const DATACENTER_TABS = [
  "Summary", "Recent activity", "Storage", "Templates", "Backups", "Exports", "Permissions", "Network",
  "Automation", "Containers", "Nodes", "HA", "Compatibility", "Notifications", "SSO", "Journal",
];
const NODE_TABS = ["Summary", "System summary", "Network", "Disk storage", "Tasks", "Compatibility", "Shell"];

// Words that would reveal untranslated French UI text.
const FRENCH = /\b(Chargement|Erreur|Annuler|Créer|Supprimer|Aucun|Aucune|Réseau|Stockage|Sauvegarde|Enregistrer|Activer|Utilisateur|Mot de passe|En cours|Terminé|Échec|Heure|Statut|Ajouter|Fermer|Démarrer|Arrêter|Actualiser|Nœud|Hôte|Disque|Mémoire|Modèle|Résumé|Tâches|Conteneurs?|Instantané|En marche|En pause|Suspendue?s?)\b|[àâçéèêëîïôûùüœ]/;

function watch(page: Page) {
  const failedApi: string[] = [];
  const problems: string[] = [];
  page.on("response", (r) => {
    const url = new URL(r.url());
    if (r.status() >= 500) failedApi.push(`${r.status()} ${r.request().method()} ${url.pathname}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) problems.push(`console: ${m.text()}`);
  });
  return { failedApi, problems };
}

test.describe("Every page renders for a signed-in administrator", () => {
  for (const tab of DATACENTER_TABS) {
    test(`Datacenter tab "${tab}" renders without errors or French text`, async ({ page }) => {
      const seen = watch(page);
      await uiLogin(page);
      await page.getByRole("tab", { name: tab, exact: true }).click();
      await expect(page.getByRole("tab", { name: tab, exact: true })).toHaveAttribute("aria-selected", "true");
      await page.waitForLoadState("networkidle");
      const text = await page.locator("main, body").first().innerText();
      const french = text.split("\n").filter((l) => FRENCH.test(l));
      expect(french, "untranslated French text").toEqual([]);
      expect(seen.problems).toEqual([]);
      expect(seen.failedApi).toEqual([]);
    });
  }

  test("Node tabs render without errors or French text", async ({ page, request }) => {
    const { hostname } = await (await request.get("/health")).json();
    const seen = watch(page);
    await uiLogin(page);
    await page.getByRole("treeitem", { name: new RegExp(hostname.split(".")[0]) }).click();
    for (const tab of NODE_TABS) {
      await page.getByRole("tab", { name: tab, exact: true }).click();
      await expect(page.getByRole("tab", { name: tab, exact: true })).toHaveAttribute("aria-selected", "true");
      await page.waitForLoadState("networkidle");
      const text = await page.locator("body").innerText();
      expect(text.split("\n").filter((l) => FRENCH.test(l)), `French text on node tab ${tab}`).toEqual([]);
    }
    expect(seen.problems).toEqual([]);
    expect(seen.failedApi).toEqual([]);
  });
});

test.describe("Automated accessibility scan (does not replace keyboard testing)", () => {
  for (const tab of ["Summary", "Storage", "Network", "Permissions", "Notifications", "Journal"]) {
    test(`no serious or critical axe violations on the "${tab}" tab`, async ({ page }) => {
      await uiLogin(page);
      await page.getByRole("tab", { name: tab, exact: true }).click();
      await page.waitForLoadState("networkidle");
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(blocking.map((v) => `${v.id}: ${v.nodes.length} node(s)`)).toEqual([]);
    });
  }
});
