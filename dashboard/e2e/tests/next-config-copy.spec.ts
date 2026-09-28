import type { Route } from "@playwright/test";
import { ADMIN, apiLogin, expect, test } from "../support/fixtures";

// Configuration copy on the Nodes page. The CI host has no second node, so the node list and the copies are
// stand-ins; the real backend is only asked for its (empty) copy status. The copy itself (snapshot, transfer,
// promotion) is covered by tests/test_config_copy.py.
test.describe.configure({ timeout: 90_000 });

const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

test("the real backend answers the copy status", async ({ request }) => {
  const token = await apiLogin(request);
  const r = await request.get("/nodes/config-copy", { headers: { Authorization: `Bearer ${token}` } });
  expect(r.status(), await r.text()).toBe(200);
  expect(Array.isArray(await r.json())).toBe(true);
});

test("each node shows its last copy, a failure its reason, and Copy now sends it again", async ({ page }) => {
  let copies = [
    { node: "peer", copie_le: new Date(Date.now() - 120_000).toISOString(), statut: "ok", taille: 81920, empreinte: "x", erreur: null },
    { node: "far", copie_le: new Date().toISOString(), statut: "echec", taille: null, empreinte: null, erreur: "Copy failed: Connection refused" },
  ];
  let posted = 0;
  await page.route(/\/nodes(\/.*)?(\?.*)?$/, async (route) => {
    const req = route.request();
    if (req.resourceType() === "document") return route.fallback();
    const path = new URL(req.url()).pathname;
    if (req.method() === "GET" && /\/nodes$/.test(path)) return json(route, [
      { id: 9, name: "peer", hostname: "peer.example", ssh_user: "root", ssh_port: 22, statut: "en_ligne" },
      { id: 10, name: "far", hostname: "far.example", ssh_user: "root", ssh_port: 22, statut: "en_ligne" },
    ]);
    if (req.method() === "GET" && /\/nodes\/(peer|far)\/summary$/.test(path)) return json(route, { connecte: true, vms_actives: 0, vms_arretees: 0 });
    if (/\/nodes\/config-copy$/.test(path)) {
      if (req.method() === "POST") {
        posted += 1;
        copies = copies.map((c) => ({ ...c, statut: "ok", erreur: null, copie_le: new Date().toISOString() }));
        return json(route, { resultats: [{ node: "peer", statut: "ok" }, { node: "far", statut: "ok" }], copies });
      }
      return json(route, copies);
    }
    return route.fallback();
  });
  await page.addInitScript(() => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "en"); } });
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=nodes");

  const card = page.getByRole("main").getByRole("region", { name: "Configuration copy" });
  await expect(card.getByRole("row", { name: /^peer/ }).getByText("Copied")).toBeVisible({ timeout: 20_000 });
  await expect(card.getByRole("row", { name: /^far/ }).getByText("Copy failed: Connection refused")).toBeVisible();
  await expect(card.getByText("/root/hyperlite/scripts/hyperlite-promote")).toBeVisible();
  await card.getByRole("button", { name: "Copy now" }).click();
  await expect(page.getByText("Configuration copied").first()).toBeVisible({ timeout: 20_000 });
  await expect(card.getByRole("row", { name: /^far/ }).getByText("Copied")).toBeVisible();
  expect(posted).toBe(1);
});
