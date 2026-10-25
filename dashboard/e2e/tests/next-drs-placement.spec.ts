import type { Page, Route } from "@playwright/test";
import { ADMIN, expect, test, apiRoute } from "../support/fixtures";

// Load balancing's placement in the dashboard (docs/design/drs.md 3.6, #334 step 5): "Auto", the default node of the
// creation wizard in a cluster, and a drain that spreads the VMs. The real backend is a single node, so the cluster
// (two members), the placement, the creation on a member and the maintenance are stand-ins; the server side is covered
// by tests/test_drs_placement.py and tests/test_node_maintenance.py.
test.describe.configure({ mode: "serial", timeout: 90_000 });

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const member = (id: number, name: string) => ({ id, name, hostname: `${name}.example`, ssh_user: "root", ssh_port: 22, status: "online", member: true });

async function open(page: Page) {
  await page.addInitScript(() => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "en"); } });
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
}

async function twoMembers(page: Page, extra?: (route: Route, path: string, url: URL) => Promise<void> | void) {
  await page.route(/\/nodes(\/.*)?(\?.*)?$/, async (route) => {
    const req = route.request();
    if (req.resourceType() === "document") return route.fallback();
    const url = new URL(req.url());
    const path = apiRoute(url.href);
    if (req.method() === "GET" && /\/nodes$/.test(path)) return json(route, [member(9, "peer"), member(10, "peer2")]);
    if (req.method() === "GET" && /\/nodes\/peer2?\/summary$/.test(path)) return json(route, { connected: true, vms_running: 0, vms_stopped: 0, storage_capacity_gb: 100, storage_available_gb: 50 });
    if (extra) {
      await extra(route, path, url);
      return;
    }
    return route.fallback();
  });
}

test("in a cluster the wizard starts on Auto and creates the VM on the member the placement chose", async ({ page }) => {
  await twoMembers(page);
  let asked: URLSearchParams | null = null;
  let created: { node: string | null; body: { name: string } } | null = null;
  await page.route(/\/drs\/placement\?/, (route) => { asked = new URL(route.request().url()).searchParams; return json(route, { node: "peer2", local: false, skipped: [] }); });
  await page.route(/\/vms\?node=/, (route) => {
    const req = route.request();
    if (req.method() !== "POST") return route.fallback();
    created = { node: new URL(req.url()).searchParams.get("node"), body: req.postDataJSON() };
    return json(route, { name: created.body.name, state: "stopped" }, 201);
  });
  await open(page);
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("menuitem", { name: "Virtual machine" }).click();
  const dlg = page.getByRole("dialog");
  await dlg.getByRole("button", { name: "Next" }).click(); // source -> identity
  await dlg.getByRole("textbox", { name: "VM name" }).fill("e2e-auto");
  await dlg.getByRole("textbox", { name: "User" }).fill("tester");
  await dlg.getByRole("textbox", { name: "Password" }).fill("Testpass1");
  await dlg.getByRole("button", { name: "Next" }).click(); // identity -> placement
  await expect(dlg.getByRole("radio", { name: /^Auto/ })).toBeChecked();
  await dlg.getByRole("button", { name: "Next" }).click(); // placement -> compute
  await dlg.getByRole("button", { name: "Next" }).click(); // compute -> storage
  await dlg.getByRole("spinbutton", { name: "Size of disk 1 in GB" }).fill("3");
  for (let i = 0; i < 3; i++) await dlg.getByRole("button", { name: "Next" }).click(); // -> review
  await dlg.getByRole("button", { name: "Create the VM" }).click();
  await expect(page.getByText("e2e-auto created on peer2").first()).toBeVisible({ timeout: 20_000 });
  expect(created).toMatchObject({ node: "peer2", body: { name: "e2e-auto" } });
  expect(asked?.get("name")).toBe("e2e-auto");
  expect(asked?.get("memory_mb")).toBe("2048");
});

test("a drain spreads the VMs by default when two members can take them, each shown with its member", async ({ page }) => {
  let posted: unknown = null;
  let state: { node: string; started_by: string; started_at: string }[] = [];
  const spread = { migratable: ["e2e-db", "e2e-web"], not_migratable: [], targets: { "e2e-db": "peer2", "e2e-web": "peer" } };
  await twoMembers(page, (route, path, url) => {
    const req = route.request();
    if (req.method() === "GET" && /\/nodes\/maintenance$/.test(path)) return json(route, state);
    if (req.method() === "GET" && /\/nodes\/local\/drain-plan$/.test(path)) {
      return json(route, url.searchParams.get("spread") === "true" ? spread : { migratable: [], not_migratable: [] });
    }
    if (req.method() === "POST" && /\/nodes\/local\/maintenance$/.test(path)) {
      posted = req.postDataJSON();
      state = [{ node: "local", started_by: "admin", started_at: new Date().toISOString() }];
      return json(route, { node: "local", in_maintenance: true, task_id: "t1", ...spread }, 202);
    }
    return route.fallback();
  });
  await open(page);
  await page.goto("/node/local?tab=summary");
  await page.getByRole("main").getByRole("button", { name: "Actions", exact: true }).click();
  await page.getByRole("menuitem", { name: /^Enter maintenance/ }).click();
  const dlg = page.getByRole("dialog", { name: /in maintenance$/ });
  await expect(dlg.getByLabel("Move the running VMs to")).toHaveValue("__spread__");
  await expect(dlg.getByRole("heading", { name: "Will be migrated (2)" })).toBeVisible();
  await expect(dlg.getByRole("listitem").filter({ hasText: "e2e-db" })).toContainText("→ peer2");
  await dlg.getByRole("button", { name: "Enter maintenance", exact: true }).click();
  await expect(page.getByText("Maintenance started").first()).toBeVisible({ timeout: 20_000 });
  expect(posted).toEqual({ spread: true });
});
