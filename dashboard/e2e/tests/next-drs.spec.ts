import type { Page, Route } from "@playwright/test";
import { ADMIN, expect, test, apiRoute } from "../support/fixtures";

// The load balancing page (docs/design/drs.md, #334). The real backend is a single node, where load balancing has
// nothing to read: GET /drs and its changes are a stateful stand-in here; the server side is covered by
// tests/test_drs_apply.py, tests/test_drs_engine.py and tests/test_drs_rules.py.
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

test("the page shows the members and the moves, applies one, and saves the settings, a rule and an exception", async ({ page }) => {
  const drs = {
    settings: { mode: "manual", threshold: 3 },
    state: {
      leading: true, unbalanced: 3, spread: 62, paused: null, running: [] as unknown[], reason: null,
      nodes: { n1: { cpu: 88, mem: 70, score: 88 }, n2: { cpu: 20, mem: 26, score: 26 } },
      moves: [{ vm: "e2e-big", source: "n1", target: "n2", gain: 31.5, reason: "n1 at 88 %, spread 62 points" }],
    },
    rules: [] as { name: string }[],
    behaviours: {} as Record<string, string>,
  };
  const sent: Record<string, unknown[]> = { settings: [], apply: [], rules: [], vms: [] };
  await page.route(/\/drs(\/.*)?$/, async (route) => {
    const req = route.request();
    if (req.resourceType() === "document") return route.fallback();
    const path = apiRoute(req.url());
    if (req.method() === "GET" && /\/drs$/.test(path)) return json(route, drs);
    if (req.method() === "PUT" && /\/drs\/settings$/.test(path)) { sent.settings.push(req.postDataJSON()); drs.settings = req.postDataJSON(); return json(route, drs.settings); }
    if (req.method() === "POST" && /\/drs\/apply$/.test(path)) { sent.apply.push(req.postDataJSON()); drs.state.running = [drs.state.moves[0]]; return json(route, { task_id: "t1", status: "running" }, 202); }
    if (req.method() === "PUT" && /\/drs\/rules\//.test(path)) { const r = req.postDataJSON(); sent.rules.push(r); drs.rules = [{ ...r, created_by: "admin" }]; return json(route, r); }
    if (req.method() === "PUT" && /\/drs\/vms\//.test(path)) { const vm = decodeURIComponent(path.split("/").pop() || ""); sent.vms.push([vm, req.postDataJSON()]); drs.behaviours = { [vm]: req.postDataJSON().behaviour }; return json(route, {}); }
    return route.fallback();
  });

  await open(page);
  await page.getByRole("navigation").getByRole("button", { name: "Load balancing" }).click();
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Load balancing", level: 1 })).toBeVisible();
  await expect(main.getByText("1 recommended move(s), waiting for an administrator.")).toBeVisible();
  await expect(main.getByRole("row", { name: /n1/ }).first()).toContainText("88 %");

  // Apply the move: confirmed first, then shown as moving.
  await main.getByRole("button", { name: "Apply the move of e2e-big" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Apply" }).click();
  await expect(main.getByText("Moving", { exact: true })).toBeVisible({ timeout: 20_000 });
  expect(sent.apply).toEqual([{ vm: "e2e-big", source: "n1", target: "n2", reason: "n1 at 88 %, spread 62 points" }]);

  // Settings: automatic, level 4.
  await main.getByLabel("Mode").selectOption("automatic");
  await main.getByLabel("Threshold").selectOption("4");
  await main.getByRole("region", { name: "Settings" }).getByRole("button", { name: "Save" }).click();
  await expect.poll(() => sent.settings.length).toBe(1);
  expect(sent.settings[0]).toEqual({ mode: "automatic", threshold: 4 });

  // A rule: two DNS servers apart, must.
  await main.getByRole("button", { name: "Add a rule" }).click();
  const drawer = page.getByRole("dialog", { name: "New rule" });
  await drawer.getByLabel("Name").fill("dns");
  await drawer.getByLabel("A VM's name").fill("dns1");
  await drawer.getByRole("button", { name: "Add", exact: true }).click();
  await drawer.getByLabel("A VM's name").fill("dns2");
  await drawer.getByLabel("A VM's name").press("Enter");
  await drawer.getByRole("radio", { name: /^Must/ }).check();
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(drawer).toHaveCount(0);
  expect(sent.rules).toEqual([{ name: "dns", kind: "apart", strength: "must", vms: ["dns1", "dns2"], nodes: [], enabled: true }]);
  await expect(main.getByRole("row", { name: /dns1, dns2/ })).toBeVisible();

  // An exception: a database never moved.
  await main.getByRole("combobox", { name: "VM", exact: true }).fill("e2e-db");
  await main.getByRole("region", { name: "VM exceptions" }).getByRole("button", { name: "Add" }).click();
  await expect.poll(() => sent.vms.length).toBe(1);
  expect(sent.vms[0]).toEqual(["e2e-db", { behaviour: "excluded" }]);
  await expect(main.getByRole("combobox", { name: "Behaviour of e2e-db" })).toHaveValue("excluded");
});

test("a paused automatic mode says why and resumes by saving the settings again; French labels", async ({ page }) => {
  let saved = 0;
  await page.route(/\/drs(\/.*)?$/, async (route) => {
    const req = route.request();
    if (req.resourceType() === "document") return route.fallback();
    const path = apiRoute(req.url());
    if (req.method() === "GET" && /\/drs$/.test(path)) {
      return json(route, { settings: { mode: "automatic", threshold: 3 }, state: { leading: true, spread: 5, moves: [], nodes: {}, running: [], paused: saved ? null : "2 migrations failed in a row (the last: web)" }, rules: [], behaviours: {} });
    }
    if (req.method() === "PUT" && /\/drs\/settings$/.test(path)) { saved += 1; return json(route, req.postDataJSON()); }
    return route.fallback();
  });
  await open(page, "fr");
  await page.getByRole("navigation").getByRole("button", { name: "Équilibrage de charge" }).click();
  const main = page.getByRole("main");
  await expect(main.getByText(/Le mode automatique est en pause : 2 migrations failed/)).toBeVisible();
  await main.getByRole("button", { name: "Reprendre" }).click();
  await expect(main.getByText("Le cluster est équilibré : rien à déplacer.")).toBeVisible({ timeout: 20_000 });
  expect(saved).toBe(1);
});
