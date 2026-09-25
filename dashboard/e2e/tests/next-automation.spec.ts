import type { Page } from "@playwright/test";
import { ADMIN, apiLogin, expect, PREFIX, test } from "../support/fixtures";

// Automation against the real backend (a harmless `echo` on the host) and Templates with a stand-in
// /templates API (the throwaway host has no template).
const stamp = Date.now().toString().slice(-6);
const JOB = `${PREFIX}job-${stamp}`;
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });
test.describe.configure({ mode: "serial", timeout: 90_000 });
test.beforeAll(async ({ request }) => { token = await apiLogin(request); });
test.afterAll(async ({ request }) => {
  const jobs = (await (await request.get("/jobs", { headers: auth() })).json()) as { id: number; name: string }[];
  for (const j of jobs) if (j.name === JOB) await request.delete(`/jobs/${j.id}`, { headers: auth() }).catch(() => {});
});

async function open(page: Page, tab: string, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/Password|Mot de passe/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto(`/datacenter?tab=${tab}`);
}

test("automation: step validation, dry run, real run behind a confirmation that lists the commands, output and deletion", async ({ page, request }) => {
  await open(page, "automation");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 2, name: /^Automation/ })).toBeVisible({ timeout: 20_000 });
  await main.getByRole("button", { name: "Create a custom job" }).click();

  await main.getByRole("button", { name: "Create", exact: true }).click(); // invalid: nothing is sent
  await expect(main.getByText("Required.").first()).toBeVisible();
  await main.getByLabel("Job name").fill(JOB);
  await main.getByLabel("shell command").fill("echo hyperlite-e2e-ok");
  await main.getByLabel("Success condition value").fill("abc");
  await main.getByRole("button", { name: "Create", exact: true }).click();
  await expect(main.getByText("Enter a whole number")).toBeVisible();
  await main.getByLabel("Success condition value").fill("0");
  await main.getByRole("button", { name: "Create", exact: true }).click();
  await expect(main.getByRole("button", { name: `Run ${JOB}`, exact: true })).toBeVisible({ timeout: 15_000 });

  await main.getByRole("button", { name: `Dry run ${JOB}` }).click();
  await expect(main.getByText("dry run", { exact: true }).first()).toBeVisible({ timeout: 20_000 });

  // real run: the confirmation names the exact command; cancelling launches nothing
  await main.getByRole("button", { name: `Run ${JOB}`, exact: true }).click();
  await expect(page.getByRole("alertdialog")).toContainText("echo hyperlite-e2e-ok");
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  const before = ((await (await request.get("/jobs", { headers: auth() })).json()) as { id: number; name: string }[]).find((j) => j.name === JOB)!;
  const runsBefore = ((await (await request.get(`/jobs/${before.id}/runs`, { headers: auth() })).json()) as { dry_run: number }[]).filter((r) => !r.dry_run).length;
  expect(runsBefore).toBe(0);
  await main.getByRole("button", { name: `Run ${JOB}`, exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Run", exact: true }).click();
  await expect(main.getByText("real run", { exact: true }).first()).toBeVisible({ timeout: 20_000 });
  await expect(main.getByText("Done").first()).toBeVisible({ timeout: 30_000 }); // history refreshed by itself

  await main.getByRole("button", { name: /^\d/ }).first().click();
  await expect(main.getByLabel("Run output")).toContainText("hyperlite-e2e-ok", { timeout: 15_000 });

  await main.getByRole("button", { name: `Delete job ${JOB}` }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(main.getByRole("button", { name: `Delete job ${JOB}` })).toHaveCount(0, { timeout: 15_000 });
});

test("templates: deploy asks for a valid, unused name; deletion is confirmed", async ({ page }) => {
  const deployed: unknown[] = []; const deleted: string[] = [];
  const tpls = [{ nom: "tpl-web", vm_source: "web-src", vcpu: 2, memoire_mo: 2048, cree_par: "admin", cree_le: "2026-09-25" }];
  await page.route(/\/templates(\/.*)?(\?.*)?$/, async (route) => {
    const req = route.request(); if (req.resourceType() === "document") return route.fallback();
    const ok = (b: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
    if (req.method() === "GET") return ok(tpls);
    if (req.method() === "POST") { deployed.push(req.postDataJSON()); return ok({ ok: true }); }
    if (req.method() === "DELETE") { deleted.push(decodeURIComponent(new URL(req.url()).pathname.split("/").pop()!)); tpls.length = 0; return ok({ ok: true }); }
    return route.fallback();
  });
  await open(page, "templates");
  const main = page.getByRole("main");
  await expect(main.getByRole("row", { name: /tpl-web/ })).toContainText("web-src");
  await main.getByRole("button", { name: "Deploy template tpl-web" }).click();
  const dlg = page.getByRole("dialog");
  await dlg.getByRole("textbox").fill("bad name!");
  await dlg.getByRole("button", { name: "Deploy", exact: true }).click();
  await expect(dlg).toContainText("Use letters, digits and hyphens");
  expect(deployed).toEqual([]);
  await dlg.getByRole("textbox").fill("web-02");
  await dlg.getByRole("button", { name: "Deploy", exact: true }).click();
  await expect.poll(() => deployed).toMatchObject([{ new_name: "web-02" }]);

  await main.getByRole("button", { name: "Delete template tpl-web" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("permanently deleted");
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
  await expect.poll(() => deleted).toEqual(["tpl-web"]);
  await expect(main.getByText("No templates")).toBeVisible({ timeout: 15_000 });
});

test("French labels and no overflow on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await open(page, "automation", "fr");
  await expect(page.getByRole("main").getByRole("heading", { level: 2, name: /Automatisation/ })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("main").getByRole("button", { name: "Créer une tâche personnalisée" }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
