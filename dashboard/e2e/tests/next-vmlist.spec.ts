import { expect, goTo, test, uiLogin } from "../support/fixtures";

// The VM list shows on which machine (node) each VM runs: grouped by node when there are several, with a node
// filter. The throwaway test host is a single node, so a second one ("peer") and its VM are simulated.
const json = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });

test("VMs are grouped by node, can be filtered by node, and the grouping can be turned off", async ({ page }) => {
  await page.route(/\/nodes(\?.*)?$/, (r) => (r.request().resourceType() === "document" ? r.fallback() : r.fulfill(json([{ name: "peer", hostname: "10.0.0.9", ssh_port: 22, ssh_user: "root", statut: "en_ligne" }]))));
  await page.route(/\/nodes\/peer\/summary/, (r) => r.fulfill(json({ connecte: true, vms_actives: 1, vms_arretees: 0 })));
  await page.route(/\/vms\?node=peer/, (r) => r.fulfill(json([{ nom: "e2e-peer-vm", etat: "actif", vcpu: 2, memoire_mo: 2048, ip: "192.168.122.40", os: "Debian 12" }])));
  await uiLogin(page);
  await goTo(page, "Virtual Machines");
  const main = page.getByRole("main");
  const table = main.getByRole("table");
  // one header row per machine, with its address and its count
  await expect(table.getByRole("row", { name: /peer 10\.0\.0\.9/ })).toContainText("10.0.0.9");
  await expect(table.getByRole("row", { name: /peer 10\.0\.0\.9/ })).toContainText("1 running / 1 VM(s)");
  await expect(table.getByRole("row", { name: /e2e-peer-vm/ })).toBeVisible();
  // the node column is not repeated while grouped
  await expect(table.getByRole("columnheader", { name: "Node", exact: true })).toHaveCount(0);

  // filter on the simulated node only
  await main.getByRole("combobox", { name: "Filter by node" }).selectOption({ label: "peer (1)" });
  await expect(table.getByRole("row", { name: /e2e-peer-vm/ })).toBeVisible();
  await expect(table.getByRole("row", { name: /peer 10\.0\.0\.9/ })).toBeVisible();
  await expect(table.getByRole("row", { name: /Local node|hl-devhub/ })).toHaveCount(0);

  // without grouping, the Node column names the machine and leads to it
  await main.getByRole("combobox", { name: "Filter by node" }).selectOption({ label: "All nodes" });
  await main.getByRole("button", { name: "Group by node" }).click();
  await expect(main.getByRole("button", { name: "Group by node" })).toHaveAttribute("aria-pressed", "false");
  const row = table.getByRole("row", { name: /e2e-peer-vm/ });
  await expect(row.getByRole("button", { name: "peer", exact: true })).toBeVisible();
  await row.getByRole("button", { name: "peer", exact: true }).click();
  await expect(page).toHaveURL(/\/node\/peer/);
});
