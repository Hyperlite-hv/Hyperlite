import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// A container's own page: reached from the list, resources and DNS edited, start at boot, its history. The
// container is served by the test; the backend part is covered by tests/test_container_config.py.
const NAME = "e2e-ct-web";
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());

test("container page: open from the list, edit resources and DNS, start at boot, see its tasks", async ({ page }) => {
  let detail = {
    nom: NAME, etat: "actif", mode: "systeme", image: null, stockage: null, id: 3, uuid: "u", vcpu: 1, memoire_mo: 512, ip: "10.0.0.5",
    utilisateur_ssh: "ops", interfaces: [{ reseau: "default", mac: "52:54:00:aa:bb:cc" }], dns: ["1.1.1.1"], demarrage_auto: false,
  };
  const patches: unknown[] = [];
  await uiLogin(page);
  await page.route(/\/containers(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([detail])) : r.fallback()));
  await page.route(new RegExp(`/containers/${NAME}(\\?.*)?$`), (r) => {
    if (!api(r)) return r.fallback();
    if (r.request().method() === "PATCH") {
      const body = r.request().postDataJSON();
      patches.push(body);
      detail = { ...detail, ...(body.vcpu ? { vcpu: body.vcpu } : {}), ...(body.memory_mb ? { memoire_mo: body.memory_mb } : {}),
        ...(body.dns ? { dns: body.dns } : {}), ...(body.demarrage_auto !== undefined ? { demarrage_auto: body.demarrage_auto } : {}) };
      return r.fulfill(json({ ...detail, a_redemarrer: Boolean(body.vcpu) }));
    }
    return r.fulfill(json(detail));
  });
  await page.route(/\/tasks\?.*objet=e2e-ct-web/, (r) => (api(r) ? r.fulfill(json([
    { id: "t1", type: "backup_container", cible: NAME, node: null, username: "admin", statut: "termine", progres: 100, cree_le: new Date().toISOString(), debut_le: new Date().toISOString(), fin_le: new Date().toISOString(), erreur: null, annulable: false, arret_propre: false, orpheline: false },
  ])) : r.fallback()));

  await page.goto("/datacenter?tab=containers");
  await page.getByRole("button", { name: NAME, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/container/${NAME}`));
  await expect(page.getByRole("heading", { level: 1, name: NAME })).toBeVisible();
  const main = page.getByRole("main");

  const res = main.getByRole("region", { name: "Resources" });
  await res.getByLabel("vCPU").fill("2");
  await res.getByLabel("Memory").fill("1024");
  await res.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Part of the change applies at the container's next start.")).toBeVisible();

  const net = main.getByRole("region", { name: "Network" });
  await expect(net.getByText("default · 52:54:00:aa:bb:cc")).toBeVisible();
  await net.getByLabel("DNS servers").fill("10.0.0.53, 9.9.9.9");
  await net.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("DNS servers saved")).toBeVisible();

  await main.getByLabel("Start this container when the host starts").check();
  await expect(page.getByText("Start at boot saved")).toBeVisible();
  expect(patches).toEqual([{ vcpu: 2, memory_mb: 1024 }, { dns: ["10.0.0.53", "9.9.9.9"] }, { demarrage_auto: true }]);

  await page.getByRole("tab", { name: "Tasks" }).click();
  await expect(main.getByRole("table")).toContainText(NAME);
  await expect(page).toHaveURL(/tab=tasks/);
});
