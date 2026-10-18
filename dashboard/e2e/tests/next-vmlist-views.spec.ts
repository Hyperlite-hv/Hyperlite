import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// The VM list grouped by tag and by pool, its optional columns, and a saved view applied again after a reload.
// VMs, tags and pools are served by the test.
const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const vm = (name: string, state: string, os: string) => ({ name, state, id: state === "active" ? 1 : null, uuid: name, vcpu: 1, memory_mb: 1024, ip: null, ssh_user: null, uptime_s: state === "active" ? 60 : null, os, zfs_storage: false, iscsi_storage: false, guest_agent: null, firmware: "bios" });

test("VM list: group by tag or pool, choose columns, save a view", async ({ page }) => {
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm("e2e-v-web", "active", "Debian"), vm("e2e-v-db", "stopped", "Rocky"), vm("e2e-v-lab", "stopped", "Alpine")])) : r.fallback()));
  await page.route(/\/meta$/, (r) => (api(r) ? r.fulfill(json([
    { kind: "vm", node: null, name: "e2e-v-web", tags: ["e2e-prod", "e2e-front"], has_notes: false },
    { kind: "vm", node: null, name: "e2e-v-db", tags: ["e2e-prod"], has_notes: false },
  ])) : r.fallback()));
  await page.route(/\/pools$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([{ id: 1, name: "e2e-team", description: "", vms: ["e2e-v-db"] }])) : r.fallback()));

  await page.goto("/datacenter?tab=vms");
  const main = page.getByRole("main");
  const table = main.getByRole("table");
  await main.getByLabel("Group by").selectOption("tag");
  await expect(table.getByRole("columnheader", { name: /e2e-front/ })).toBeVisible();
  const prod = table.locator("tbody", { has: page.getByRole("columnheader", { name: /e2e-prod/ }) });
  await expect(prod.getByRole("row")).toHaveCount(3); // band + 2 VMs
  await expect(table.locator("tbody", { has: page.getByRole("columnheader", { name: /No tag/ }) }).getByRole("row", { name: /e2e-v-lab/ })).toBeVisible();

  await main.getByLabel("Group by").selectOption("pool");
  await expect(table.locator("tbody", { has: page.getByRole("columnheader", { name: /e2e-team/ }) }).getByRole("row", { name: /e2e-v-db/ })).toBeVisible();

  await main.locator("summary", { hasText: "Columns" }).click();
  await main.getByRole("checkbox", { name: "System" }).check();
  await main.getByRole("checkbox", { name: "Uptime" }).uncheck();
  await expect(table.getByRole("columnheader", { name: "System" })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: /Uptime/ })).toHaveCount(0);
  await expect(table.getByRole("row", { name: /e2e-v-db.*Rocky/ })).toBeVisible();

  await main.getByLabel("Filter by tag").selectOption("e2e-prod");
  await main.getByRole("button", { name: "Save the view" }).click();
  await page.getByRole("dialog").getByLabel("Name").fill("Prod");
  await page.getByRole("dialog").getByRole("button", { name: "Save the view" }).click();

  await page.reload();
  await main.getByLabel("Filter by tag").selectOption("");
  await main.getByLabel("Group by").selectOption("none");
  await expect(table.getByRole("row", { name: /e2e-v-lab/ })).toBeVisible();
  await main.getByLabel("Saved views").selectOption("Prod");
  await expect(main.getByLabel("Group by")).toHaveValue("pool");
  await expect(table.getByRole("row", { name: /e2e-v-lab/ })).toHaveCount(0);
  await expect(table.getByRole("columnheader", { name: "System" })).toBeVisible();

  await main.getByRole("button", { name: "Delete the view Prod" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
  await expect(main.getByLabel("Saved views").locator("option")).toHaveCount(1);
});
