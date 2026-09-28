import type { Page, Route } from "@playwright/test";
import { ADMIN, apiLogin, expect, test } from "../support/fixtures";

// Node maintenance from the node's Actions menu. The spec files share one backend and run in parallel, so the
// real host is never put in maintenance here (that would refuse the VM creations of the other files): the
// maintenance endpoints and a second node are stateful stand-ins, and the real backend is only asked for a
// read-only drain plan. The server side (marking, draining, refusals) is covered by tests/test_node_maintenance.py.
test.describe.configure({ mode: "serial", timeout: 90_000 });

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

async function open(page: Page, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
}

test("the real backend answers a read-only drain plan for this host", async ({ request }) => {
  const token = await apiLogin(request);
  const r = await request.get("/nodes/local/drain-plan", { headers: { Authorization: `Bearer ${token}` } });
  expect(r.status(), await r.text()).toBe(200);
  const plan = (await r.json()) as { migrables: string[]; non_migrables: { nom: string; raison: string }[] };
  // No target: nothing moves, and every VM of the host is listed with a reason.
  expect(plan.migrables).toEqual([]);
  for (const vm of plan.non_migrables) expect(vm.raison.length).toBeGreaterThan(0);
});

test("enter maintenance: the plan is shown per target, the drain is confirmed, the banner ends it", async ({ page }) => {
  let state: { node: string; started_by: string; started_at: string }[] = [];
  let posted: unknown = null;
  let deleted = 0;
  const plans = {
    peer: { migrables: ["e2e-web"], non_migrables: [{ nom: "e2e-off", raison: "Stopped: only running VMs are live-migrated, it stays on this node" }] },
    none: { migrables: [], non_migrables: [{ nom: "e2e-web", raison: "No target chosen" }, { nom: "e2e-off", raison: "Stopped" }] },
  };
  await page.route(/\/nodes(\/.*)?(\?.*)?$/, async (route) => {
    const req = route.request();
    if (req.resourceType() === "document") return route.fallback();
    const url = new URL(req.url());
    const path = url.pathname;
    if (req.method() === "GET" && /\/nodes$/.test(path)) return json(route, [{ id: 9, name: "peer", hostname: "peer.example", ssh_user: "root", ssh_port: 22, statut: "en_ligne" }]);
    if (req.method() === "GET" && /\/nodes\/peer\/summary$/.test(path)) return json(route, { connecte: true, vms_actives: 0, vms_arretees: 0, stockage_capacite_go: 100, stockage_disponible_go: 50 });
    if (req.method() === "GET" && /\/nodes\/maintenance$/.test(path)) return json(route, state);
    if (req.method() === "GET" && /\/nodes\/local\/drain-plan$/.test(path)) return json(route, url.searchParams.get("target_node") === "peer" ? plans.peer : plans.none);
    if (req.method() === "POST" && /\/nodes\/local\/maintenance$/.test(path)) {
      posted = req.postDataJSON();
      state = [{ node: "local", started_by: "admin", started_at: new Date().toISOString() }];
      return json(route, { node: "local", en_maintenance: true, task_id: "t1", ...plans.peer }, 202);
    }
    if (req.method() === "DELETE" && /\/nodes\/local\/maintenance$/.test(path)) { deleted += 1; state = []; return json(route, { node: "local", en_maintenance: false }); }
    return route.fallback();
  });

  await open(page);
  await page.goto("/node/local?tab=summary");
  const main = page.getByRole("main");
  await main.getByRole("button", { name: "Actions", exact: true }).click();
  await page.getByRole("menuitem", { name: /^Enter maintenance/ }).click();
  const dlg = page.getByRole("dialog", { name: /in maintenance$/ });
  // The only other online node is preselected, and the plan says what moves and why the rest stays.
  await expect(dlg.getByLabel("Move the running VMs to")).toHaveValue("peer");
  await expect(dlg.getByRole("heading", { name: "Will be migrated (1)" })).toBeVisible();
  await expect(dlg.getByText("e2e-web", { exact: true })).toBeVisible();
  await expect(dlg.getByRole("heading", { name: "Will stay on this node (1)" })).toBeVisible();
  await expect(dlg.getByText(/^Stopped: only running VMs/)).toBeVisible();
  await dlg.getByLabel("Move the running VMs to").selectOption("");
  await expect(dlg.getByRole("heading", { name: "Will be migrated (0)" })).toBeVisible();
  await dlg.getByLabel("Move the running VMs to").selectOption("peer");
  await expect(dlg.getByRole("heading", { name: "Will be migrated (1)" })).toBeVisible();
  await dlg.getByRole("button", { name: "Enter maintenance", exact: true }).click();
  await expect(page.getByText("Maintenance started").first()).toBeVisible({ timeout: 20_000 });
  expect(posted).toEqual({ target_node: "peer" });

  await expect(main.getByText(/^In maintenance since/)).toBeVisible({ timeout: 20_000 });
  await main.getByRole("button", { name: "End maintenance" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  expect(deleted).toBe(0);
  await main.getByRole("button", { name: "End maintenance" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "End maintenance" }).click();
  await expect(main.getByText(/^In maintenance since/)).toHaveCount(0, { timeout: 20_000 });
  expect(deleted).toBe(1);

  // French labels.
  await page.evaluate(() => localStorage.setItem("hyperlite-next-lang", "fr"));
  await page.reload();
  await page.getByRole("main").getByRole("button", { name: "Actions", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: /^Mettre en maintenance/ })).toBeVisible();
});
