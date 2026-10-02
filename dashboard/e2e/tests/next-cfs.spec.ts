import { expect, goTo, test, uiLogin } from "../support/fixtures";

// Administration › Replicated configuration: shadow mode of hyperlite-cfs. The test backend runs without the daemon and
// with shadow mode off, so the first test reads the real answer; the second plays a node where it is on, with
// differences, through the routes of /cfs/shadow (app/repositories/cfs/shadow.py is covered by
// tests/test_cfs_shadow.py, against the real daemon too).
test("with shadow mode off, the page says how to turn it on, in English and in French", async ({ page }) => {
  await uiLogin(page);
  await goTo(page, "Replicated configuration");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Shadow mode is off" })).toBeVisible();
  await expect(main.getByLabel("Commands to turn shadow mode on")).toContainText("HYPERLITE_CFS_SHADOW=1");
  await expect(main.getByRole("button", { name: "Copy the database again" })).toHaveCount(0);

  await page.evaluate(() => localStorage.setItem("hyperlite-next-lang", "fr"));
  await page.reload();
  await expect(main.getByRole("heading", { name: "Le mode fantôme est désactivé" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("navigation", { name: /./ }).getByRole("button", { name: "Configuration répliquée" })).toBeVisible();
});

const PATHS = { manquants: ["/meta/vm/local/e2e-web"], en_trop: ["/meta/vm/local/e2e-gone"], differents: [] };

function report(gaps: number) {
  return {
    actif: true, socket: "/run/hyperlite-cfs/socket", copies: 12, echecs: gaps ? 1 : 0, joignable: true,
    derniere_erreur: gaps ? "/meta/vm/local/e2e-web: hyperlite-cfs is not running: nothing answers on its socket" : null,
    derniere_erreur_le: gaps ? "2026-10-02T12:00:00+00:00" : null,
    demon: { mode: "local", quorum: true, version: 40, entrees: 3 },
    domaines: { meta: {
      entrees: 3, manquants: gaps ? 1 : 0, en_trop: gaps ? 1 : 0, differents: 0,
      exemples: gaps ? PATHS : { manquants: [], en_trop: [], differents: [] },
    } },
    ecarts: gaps,
  };
}

test("differences are listed, and copying the database again after a confirmation clears them", async ({ page }) => {
  let gaps = 2;
  let seeded = 0;
  await page.route(/\/cfs\/shadow$/, (route) => route.fulfill({ json: report(gaps) }));
  await page.route(/\/cfs\/shadow\/seed$/, (route) => { seeded += 1; gaps = 0; return route.fulfill({ json: { ecrits: 1, supprimes: 1 } }); });
  await uiLogin(page);
  await goTo(page, "Replicated configuration");
  const main = page.getByRole("main");

  const strip = main.getByRole("group", { name: "State of shadow mode" });
  await expect(strip).toContainText("Local mode");
  await expect(strip).toContainText("Writable");
  await expect(strip.locator(".nx-kpi", { hasText: "Differences" })).toContainText("2");
  await expect(main.getByRole("status").filter({ hasText: "Last failure" })).toContainText("nothing answers on its socket");
  const row = main.getByRole("row", { name: /Notes and tags/ });
  await expect(row).toContainText("3");
  await main.getByText("Entries that differ: Notes and tags").click();
  await expect(main.getByRole("listitem").filter({ hasText: "/meta/vm/local/e2e-web" })).toBeVisible();
  await expect(main.getByRole("listitem").filter({ hasText: "/meta/vm/local/e2e-gone" })).toBeVisible();

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
  await goTo(page, "Replicated configuration");
  const main = page.getByRole("main");
  await expect(main.getByRole("alert")).toContainText("hyperlite-cfs is not running");
  await expect(main.getByRole("group", { name: "State of shadow mode" })).toContainText("Not reachable");
  await expect(main.getByRole("button", { name: "Copy the database again" })).toBeDisabled();
});
