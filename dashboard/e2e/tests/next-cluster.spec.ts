import type { Page } from "@playwright/test";
import { expect, test, ADMIN } from "../support/fixtures";

// Nodes, High availability and Compatibility pages. The throwaway host has no second machine, so the
// remote-node and HA endpoints are replaced by stateful stand-ins where a peer is needed.
test.describe.configure({ mode: "serial", timeout: 90_000 });

async function open(page: Page, tab: string) {
  await page.addInitScript(() => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "en"); } });
  await page.goto("/");
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto(`/datacenter?tab=${tab}`);
}
const json = (route: import("@playwright/test").Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

test("Nodes: this host is listed and opens its page, add form sends the SSH port, failure is reported, removal is confirmed", async ({ page }) => {
  const remote = [{ id: 7, name: "peer", hostname: "10.0.0.9", ssh_user: "root", ssh_port: 2222, statut: "hors_ligne" }];
  let added: unknown = null; const removed: string[] = [];
  await page.route(/\/nodes(\/.*)?(\?.*)?$/, async (route) => {
    const req = route.request(); if (req.resourceType() === "document") return route.fallback();
    const path = new URL(req.url()).pathname;
    if (req.method() === "GET" && /\/nodes$/.test(path) && added === "list") return json(route, remote);
    if (req.method() === "POST" && /\/nodes$/.test(path)) { added = req.postDataJSON(); return json(route, { detail: "SSH connection refused" }, 400); }
    if (req.method() === "GET" && /\/summary$/.test(path)) return json(route, { vms_actives: 3, vms_arretees: 1, stockage_capacite_go: 500, stockage_disponible_go: 120 });
    if (req.method() === "DELETE") { removed.push(path.split("/").pop()!); remote.length = 0; return json(route, { ok: true }); }
    return route.fallback();
  });
  await open(page, "nodes");
  const main = page.getByRole("main");
  const hostRow = main.getByRole("row", { name: /this host/ });
  await expect(hostRow).toContainText("Local (libvirt)");
  await expect(hostRow.getByRole("button", { name: "Remove node" })).toHaveCount(0); // the local host cannot be removed
  await main.getByRole("button", { name: "Add a remote node" }).click();
  await main.getByLabel("Name", { exact: true }).fill("peer");
  await main.getByLabel("IP address or hostname").fill("10.0.0.9");
  await main.getByLabel("SSH port").fill("2222");
  await main.getByRole("button", { name: "Test and add" }).click();
  await expect.poll(() => added).toMatchObject({ name: "peer", hostname: "10.0.0.9", ssh_port: 2222, ssh_user: "root" });
  await expect(page.getByText("SSH connection refused").first()).toBeVisible();
  added = "list"; // the peer is now registered
  await page.reload();
  const row = main.getByRole("row", { name: /peer/ });
  await expect(row).toContainText("root@10.0.0.9:2222");
  await expect(row).toContainText("Offline");
  await expect(row).toContainText("3");
  await main.getByRole("button", { name: "Remove node peer" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  expect(removed).toEqual([]);
  await main.getByRole("button", { name: "Remove node peer" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove", exact: true }).click();
  await expect.poll(() => removed).toEqual(["peer"]);
});

test("HA: empty state, then a down node offers a manual recovery that stays disabled until a target is chosen", async ({ page }) => {
  let recovered: unknown = null;
  let rows: unknown[] = [];
  await page.route(/\/ha(\/.*)?(\?.*)?$/, async (route) => {
    const req = route.request(); if (req.resourceType() === "document") return route.fallback();
    if (req.method() === "GET") return json(route, rows);
    if (req.method() === "POST" && req.url().endsWith("/recover")) { recovered = req.postDataJSON(); rows = []; return json(route, { ok: true }); }
    return route.fallback();
  });
  await open(page, "ha");
  const main = page.getByRole("main");
  await expect(main.getByText("No protected VM")).toBeVisible();
  rows = [{ vm_name: "vm-ha", node: "ghost", statut_noeud: "hors_ligne", last_synced_at: null }];
  await main.getByRole("button", { name: "Refresh" }).click();
  const row = main.getByRole("row", { name: /vm-ha/ });
  await expect(row).toContainText("Never");
  await expect(row.getByRole("button", { name: "Recover vm-ha" })).toBeDisabled(); // no target chosen: nothing can fire
  await expect(row.getByRole("button", { name: "Disable HA for vm-ha" })).toBeVisible();
});

test("Compatibility page lists this host's capabilities and stays inside the viewport", async ({ page }) => {
  await open(page, "compat");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Node comparison" })).toBeVisible();
  await expect(main.getByRole("table")).toBeVisible({ timeout: 20_000 });
  await expect(main.getByRole("checkbox", { name: "Differences only" })).toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
