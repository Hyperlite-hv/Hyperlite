import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());

test("notes and tags of a node: edited, validated, kept as plain text after a reload", async ({ page }) => {
  await uiLogin(page);
  await page.goto("/node/local");
  const card = page.getByRole("main").getByRole("region", { name: "Notes and tags" });
  await card.getByRole("button", { name: "Edit" }).click();
  const text = "Rack A, slot 4.\n<b>not bold</b> — ask the network team before rebooting.";
  await card.getByLabel("Notes").fill(text);
  await card.getByLabel("Tags").fill("Rack-A, not a tag!");
  await expect(card.getByText(/is not a valid tag/)).toBeVisible();
  await expect(card.getByRole("button", { name: "Save" })).toBeDisabled();
  await card.getByLabel("Tags").fill("Rack-A, e2e-notes, rack-a");
  await card.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Notes and tags saved")).toBeVisible();

  await page.reload();
  const again = page.getByRole("main").getByRole("region", { name: "Notes and tags" });
  await expect(again.getByText("rack-a", { exact: true })).toBeVisible();
  await expect(again.getByText("e2e-notes", { exact: true })).toBeVisible();
  // Shown as text: the markup is visible as typed, never interpreted.
  await expect(again.getByText("<b>not bold</b>", { exact: false })).toBeVisible();
  await expect(again.locator("b")).toHaveCount(0);

  // Clean up for the other tests.
  await again.getByRole("button", { name: "Edit" }).click();
  await again.getByLabel("Notes").fill("");
  await again.getByLabel("Tags").fill("");
  await again.getByRole("button", { name: "Save" }).click();
  await expect(again.getByText(/No notes yet/)).toBeVisible();
});

test("tags in the VM list: shown under the name, a click or the menu filters by tag, search finds them", async ({ page }) => {
  const vm = (nom: string) => ({ nom, etat: "arrete", id: null, uuid: nom, vcpu: 1, memoire_mo: 512, ip: null, utilisateur_ssh: null, uptime_s: null, os: "Debian", stockage_zfs: false, stockage_iscsi: false, agent_invite: null, firmware: "bios" });
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm("e2e-tag-db"), vm("e2e-tag-web"), vm("e2e-tag-lab")])) : r.fallback()));
  await page.route(/\/meta(\?.*)?$/, (r) => (api(r) ? r.fulfill(json([
    { kind: "vm", node: "local", nom: "e2e-tag-db", tags: ["prod", "db"], a_des_notes: true },
    { kind: "vm", node: "local", nom: "e2e-tag-web", tags: ["prod"], a_des_notes: false },
  ])) : r.fallback()));
  await page.goto("/datacenter?tab=vms");
  const table = page.getByRole("table");
  await expect(table.getByRole("button", { name: "db", exact: true })).toBeVisible();

  await table.getByRole("button", { name: "db", exact: true }).click();
  await expect(table.getByRole("button", { name: "e2e-tag-db", exact: true })).toBeVisible();
  await expect(table.getByRole("button", { name: "e2e-tag-web", exact: true })).toHaveCount(0);

  const filter = page.getByLabel("Filter by tag");
  await expect(filter).toHaveValue("db");
  await filter.selectOption("prod");
  await expect(table.getByRole("button", { name: "e2e-tag-web", exact: true })).toBeVisible();
  await expect(table.getByRole("button", { name: "e2e-tag-lab", exact: true })).toHaveCount(0);

  await filter.selectOption("");
  await page.getByLabel("Filter VMs").fill("db");
  await expect(table.getByRole("button", { name: "e2e-tag-db", exact: true })).toBeVisible();
  await expect(table.getByRole("button", { name: "e2e-tag-lab", exact: true })).toHaveCount(0);
});
