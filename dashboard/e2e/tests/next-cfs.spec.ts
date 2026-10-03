import { expect, goTo, test, uiLogin } from "../support/fixtures";

// Cluster › Cluster: shadow mode of hyperlite-cfs. The test backend runs without the daemon and
// with shadow mode off, so the first test reads the real answer; the second plays a node where it is on, with
// differences, through the routes of /cfs/shadow (app/repositories/cfs/shadow.py is covered by
// tests/test_cfs_shadow.py, against the real daemon too).
test("with shadow mode off and no daemon installed, the page says so, in English and in French", async ({ page }) => {
  await uiLogin(page);
  await goTo(page, "Cluster");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Shadow mode is off" })).toBeVisible();
  // The test backend runs from a checkout: the package's /usr/local/sbin/hyperlite-cfs is not there.
  await expect(main.getByRole("alert")).toContainText("hyperlite-cfs is not installed on this node");
  await expect(main.getByRole("button", { name: "Turn shadow mode on" })).toHaveCount(0);
  await expect(main.getByRole("button", { name: "Copy the database again" })).toHaveCount(0);

  await page.evaluate(() => localStorage.setItem("hyperlite-next-lang", "fr"));
  await page.reload();
  await expect(main.getByRole("heading", { name: "Le mode fantôme est désactivé" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("navigation", { name: /./ }).getByRole("button", { name: "Cluster", exact: true })).toBeVisible();
});

const PATHS = { manquants: ["/db/object_meta/vm/local/e2e-web"], en_trop: ["/db/object_meta/vm/local/e2e-gone"], differents: [] };

function report(gaps: number) {
  return {
    actif: true, socket: "/run/hyperlite-cfs/socket", copies: 12, echecs: gaps ? 1 : 0, joignable: true,
    derniere_erreur: gaps ? "/db/object_meta/vm/local/e2e-web: hyperlite-cfs is not running: nothing answers on its socket" : null,
    derniere_erreur_le: gaps ? "2026-10-02T12:00:00+00:00" : null,
    demon: { mode: "local", quorum: true, version: 40, entrees: 3 },
    domaines: { object_meta: {
      entrees: 3, manquants: gaps ? 1 : 0, en_trop: gaps ? 1 : 0, differents: 0,
      exemples: gaps ? PATHS : { manquants: [], en_trop: [], differents: [] },
    } },
    ecarts: gaps, en_attente: 0,
  };
}

test("differences are listed, and copying the database again after a confirmation clears them", async ({ page }) => {
  let gaps = 2;
  let seeded = 0;
  await page.route(/\/cfs\/shadow$/, (route) => route.fulfill({ json: report(gaps) }));
  await page.route(/\/cfs\/shadow\/seed$/, (route) => { seeded += 1; gaps = 0; return route.fulfill({ json: { ecrits: 1, supprimes: 1 } }); });
  await uiLogin(page);
  await goTo(page, "Cluster");
  const main = page.getByRole("main");

  const strip = main.getByRole("group", { name: "State of shadow mode" });
  await expect(strip).toContainText("Local mode");
  await expect(strip).toContainText("Writable");
  await expect(strip.locator(".nx-kpi", { hasText: "Differences" })).toContainText("2");
  await expect(main.getByRole("status").filter({ hasText: "Last failure" })).toContainText("nothing answers on its socket");
  const row = main.getByRole("row", { name: /Notes and tags/ });
  await expect(row).toContainText("3");
  await main.getByText("Entries that differ: Notes and tags").click();
  await expect(main.getByRole("listitem").filter({ hasText: "/db/object_meta/vm/local/e2e-web" })).toBeVisible();
  await expect(main.getByRole("listitem").filter({ hasText: "/db/object_meta/vm/local/e2e-gone" })).toBeVisible();

  // Cancelling the confirmation sends nothing.
  await main.getByRole("button", { name: "Copy the database again" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  expect(seeded).toBe(0);

  await main.getByRole("button", { name: "Copy the database again" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Copy the database again" }).click();
  await expect(page.getByText("1 written, 1 deleted.")).toBeVisible();
  await expect(main.getByText("hyperlite-cfs holds exactly what the database holds.")).toBeVisible();
  expect(seeded).toBe(1);
});

test("a daemon that does not answer is shown, and the copy is not offered", async ({ page }) => {
  await page.route(/\/cfs\/shadow$/, (route) => route.fulfill({ json: {
    actif: true, socket: "/run/hyperlite-cfs/socket", copies: 0, echecs: 0, derniere_erreur: null, derniere_erreur_le: null,
    joignable: false, erreur: "hyperlite-cfs is not running: nothing answers on its socket",
  } }));
  await uiLogin(page);
  await goTo(page, "Cluster");
  const main = page.getByRole("main");
  await expect(main.getByRole("alert")).toContainText("hyperlite-cfs is not running");
  await expect(main.getByRole("group", { name: "State of shadow mode" })).toContainText("Not reachable");
  await expect(main.getByRole("button", { name: "Copy the database again" })).toBeDisabled();
});

test("the button turns shadow mode on, and off after a confirmation", async ({ page }) => {
  let on = false;
  const calls: string[] = [];
  await page.route(/\/cfs\/shadow$/, (route) => route.fulfill({ json: on ? { ...report(0), force: false, installe: true }
    : { actif: false, force: false, installe: true, socket: "/run/hyperlite-cfs/socket", copies: 0, echecs: 0,
        derniere_erreur: null, derniere_erreur_le: null, joignable: false, erreur: "hyperlite-cfs is not running: nothing answers on its socket" } }));
  await page.route(/\/cfs\/shadow\/activer$/, (route) => { calls.push("on"); on = true; return route.fulfill({ json: { ecrits: 3, supprimes: 0 } }); });
  await page.route(/\/cfs\/shadow\/desactiver$/, (route) => { calls.push("off"); on = false; return route.fulfill({ json: { actif: false } }); });
  await uiLogin(page);
  await goTo(page, "Cluster");
  const main = page.getByRole("main");

  await main.getByRole("button", { name: "Turn shadow mode on" }).click();
  await expect(page.getByText("Shadow mode is on")).toBeVisible();
  await expect(main.getByRole("group", { name: "State of shadow mode" })).toContainText("Local mode");
  expect(calls).toEqual(["on"]);

  await main.getByRole("button", { name: "Turn off" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  expect(calls).toEqual(["on"]);
  await main.getByRole("button", { name: "Turn off" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Turn off" }).click();
  await expect(main.getByRole("heading", { name: "Shadow mode is off" })).toBeVisible();
  expect(calls).toEqual(["on", "off"]);
});

test("in a cluster, changes received from other nodes are counted and a held-back apply is shown", async ({ page }) => {
  const problem = "hyperlite-cfs holds version 3, older than the 40 this node applied: its database was reset or replaced.";
  await page.route(/\/cfs\/shadow$/, (route) => route.fulfill({ json: {
    ...report(0), force: false, installe: true, demon: { mode: "cluster", quorum: true, version: 3, entrees: 3 },
    reception: { appliques: 7, derniere_application: "2026-10-02T12:00:00+00:00", probleme: problem },
  } }));
  await uiLogin(page);
  await goTo(page, "Cluster");
  const main = page.getByRole("main");
  const strip = main.getByRole("group", { name: "State of shadow mode" });
  await expect(strip).toContainText("Cluster mode");
  await expect(strip.locator(".nx-kpi", { hasText: "Received from other nodes" })).toContainText("7");
  await expect(main.getByRole("alert").filter({ hasText: "older than the 40" })).toBeVisible();
});
