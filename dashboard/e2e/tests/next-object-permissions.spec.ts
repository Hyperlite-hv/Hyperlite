import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// A VM's Permissions tab: the assignments on the VM and those inherited from its pools, one added and one removed.
// The ACL endpoints are served by the test; the backend part is covered by tests/test_permissions.py.
const NAME = "e2e-perm-web";
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const vm = { nom: NAME, etat: "arrete", id: null, uuid: NAME, vcpu: 1, memoire_mo: 1024, ip: null, utilisateur_ssh: null, uptime_s: null, os: "Debian", stockage_zfs: false, stockage_iscsi: false, agent_invite: null, firmware: "bios" };
const row = (id: number, subject: string, role: string, pool: string | null) => ({
  id, subject_type: "user", subject_id: subject, subject_label: subject, role, resource_type: pool ? "pool" : "vm", resource_id: pool ? "1" : NAME, resource_label: pool || NAME, herite_de: pool,
});

test("VM permissions tab: own and inherited assignments, add and remove", async ({ page }) => {
  let acl = [row(1, "bob", "operateur", null), row(2, "erin", "lecteur", "web")];
  const created: unknown[] = [];
  const deleted: string[] = [];
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(new RegExp(`/acl/object/vm/${NAME}$`), (r) => (api(r) ? r.fulfill(json(acl)) : r.fallback()));
  await page.route(/\/users$/, (r) => (api(r) ? r.fulfill(json([{ username: "admin", role: "admin" }, { username: "bob", role: "observateur" }, { username: "carol", role: "observateur" }])) : r.fallback()));
  await page.route(/\/groups$/, (r) => (api(r) ? r.fulfill(json([])) : r.fallback()));
  await page.route(/\/acl\/roles$/, (r) => (api(r) ? r.fulfill(json({ lecteur: { label: "Reader", description: "View" }, operateur: { label: "Operator", description: "Power" } })) : r.fallback()));
  await page.route(/\/acl\/custom-roles$/, (r) => (api(r) ? r.fulfill(json([])) : r.fallback()));
  await page.route(/\/acl(\/\d+)?$/, (r) => {
    if (!api(r)) return r.fallback();
    if (r.request().method() === "POST") {
      const body = r.request().postDataJSON();
      created.push(body);
      acl = [...acl, row(3, body.subject_id, body.role, null)];
      return r.fulfill(json({ id: 3 }, 201));
    }
    if (r.request().method() === "DELETE") {
      const id = r.request().url().split("/").pop() as string;
      deleted.push(id);
      acl = acl.filter((a) => String(a.id) !== id);
      return r.fulfill(json({ message: "ok" }));
    }
    return r.fallback();
  });

  await page.goto(`/vm/${NAME}?tab=summary`);
  await page.getByRole("tab", { name: "Permissions", exact: true }).click();
  const main = page.getByRole("main");
  const table = main.getByRole("region", { name: "Who has rights here" });
  await expect(table.getByRole("row", { name: /bob.*Operator.*This object/ })).toBeVisible();
  await expect(table.getByRole("row", { name: /erin.*Reader.*Pool web/ })).toBeVisible();
  await expect(table.getByRole("button", { name: "Remove the assignment of erin" })).toHaveCount(0);

  const add = main.getByRole("region", { name: "Add an assignment" });
  await expect(add.getByLabel("Subject").locator("option")).toHaveText(["Choose…", "bob", "carol"]);
  await add.getByLabel("Subject").selectOption("carol");
  await add.getByLabel("Role").selectOption("lecteur");
  await add.getByRole("button", { name: "Assign" }).click();
  await expect(table.getByRole("row", { name: /carol.*Reader.*This object/ })).toBeVisible();

  await table.getByRole("button", { name: "Remove the assignment of bob" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove" }).click();
  await expect(table.getByRole("row", { name: /bob/ })).toHaveCount(0);

  expect(created).toEqual([{ subject_type: "user", subject_id: "carol", role: "lecteur", resource_type: "vm", resource_id: NAME }]);
  expect(deleted).toEqual(["1"]);
});
