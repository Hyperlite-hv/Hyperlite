import type { Page, Route } from "@playwright/test";
import { ADMIN, apiLogin, expect, test } from "../support/fixtures";

// Automatic HA in dry-run mode on the real backend: the banner, the witness settings, fencing for this host (the
// test only reads a power state; the CI runner has no BMC, so it reports why) and the lease check. A protected VM on
// a failed node is a stand-in: the spec files share one host and no second node exists here.
test.describe.configure({ mode: "serial", timeout: 90_000 });

const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function open(page: Page, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=ha");
}

test.afterAll(async ({ request }) => {
  const auth = { Authorization: `Bearer ${await apiLogin(request)}` };
  await request.delete("/ha/fencing/local", { headers: auth }).catch(() => {});
  await request.put("/ha/settings", { headers: auth, data: { temoin: "", seuil_suspect: 3, seuil_panne: 6 } }).catch(() => {});
});

test("the HA page runs as a dry run, saves the witness, and fencing is only ever tested", async ({ page, request }) => {
  await open(page);
  const main = page.getByRole("main");
  await expect(main.getByText("Automatic HA: dry run.")).toBeVisible({ timeout: 20_000 });

  const settings = main.getByRole("region", { name: "Witness and detection" });
  await settings.getByLabel("Witness").fill("nas; reboot");
  await settings.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/must be a host name or an IP address/).first()).toBeVisible({ timeout: 20_000 });
  await settings.getByLabel("Witness").fill("nas.example.lan");
  await settings.getByLabel("Suspect after").fill("6");
  await expect(settings.getByText("1 ≤ suspect < failed ≤ 60.")).toBeVisible();
  await settings.getByLabel("Suspect after").fill("2");
  await settings.getByRole("button", { name: "Save" }).click();
  const auth = { Authorization: `Bearer ${await apiLogin(request)}` };
  await expect.poll(async () => ((await (await request.get("/ha/status", { headers: auth })).json()) as { reglages: { temoin: string; seuil_suspect: number } }).reglages, { timeout: 20_000 }).toMatchObject({ temoin: "nas.example.lan", seuil_suspect: 2 });

  const fencing = main.getByRole("region", { name: "Fencing" });
  const row = fencing.getByRole("row").nth(1); // this host comes first
  await row.getByRole("button", { name: "Configure" }).click();
  const drawer = page.getByRole("dialog", { name: /^Fencing of / });
  await drawer.getByLabel("Method").selectOption("ipmi");
  await drawer.getByLabel("BMC address").fill("192.0.2.20");
  await drawer.getByLabel("User").fill("ADMIN");
  await expect(drawer.getByRole("button", { name: "Save" })).toBeDisabled(); // no password yet
  await drawer.getByLabel("Password").fill("not-a-real-bmc");
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(row.getByText(/IPMI/)).toBeVisible({ timeout: 20_000 });
  const stored = (await (await request.get("/ha/fencing", { headers: auth })).json()) as Record<string, unknown>[];
  expect(JSON.stringify(stored)).not.toContain("not-a-real-bmc");

  // No BMC answers at a documentation address: the test reports why, it never powers anything.
  await row.getByRole("button", { name: /^Test the fencing of / }).click();
  await expect(row.locator(".is-error").first()).toBeVisible({ timeout: 60_000 });
  await row.getByRole("button", { name: /^Check the leases of / }).click();
  await expect(row.getByText(/^Leases: /)).toBeVisible({ timeout: 20_000 });

  await row.getByRole("button", { name: "Remove" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove" }).click();
  await expect(row.getByText("Not set", { exact: true })).toBeVisible({ timeout: 20_000 });
});

test("a protected VM on a failed node shows what automatic HA would have done", async ({ page }) => {
  await page.route(/\/ha(\?.*)?$/, (route) => (route.request().resourceType() === "document" ? route.fallback() : json(route, [{
    vm_name: "e2e-ha-db", node: "local", statut_noeud: "en_ligne", last_synced_at: new Date().toISOString(), etat_ha: "en_panne",
    derniere_action: "Manual recovery: Two nodes and no witness", derniere_action_le: new Date().toISOString(),
  }])));
  await open(page, "fr");
  const main = page.getByRole("main");
  await expect(main.getByText("HA automatique : mode essai.")).toBeVisible({ timeout: 20_000 });
  const row = main.getByRole("row", { name: /e2e-ha-db/ });
  await expect(row.getByText("En panne", { exact: true })).toBeVisible();
  await expect(row.getByText("Manual recovery: Two nodes and no witness")).toBeVisible();
});
