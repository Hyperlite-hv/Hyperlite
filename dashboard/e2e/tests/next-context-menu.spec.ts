import { expect, goTo, test, uiLogin } from "../support/fixtures";

// Right click on a VM or a node in a list opens its actions at the pointer: the same entries as the Actions menu
// of its page, with the same rules (unavailable entries say why). The VM is added to the list answer, so the test
// does not depend on the VMs of the test machine; no action is run.
const FAKE = { nom: "e2e-ctx-demo", etat: "arrete", vcpu: 1, memoire_mo: 512, ip: null, uuid: "00000000-0000-0000-0000-0000000c7c70", os: null, uptime_s: null };

test("right click on a VM row opens its actions at the pointer", async ({ page }) => {
  await page.route("**/vms", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const res = await route.fetch();
    return route.fulfill({ response: res, json: [...(await res.json()), FAKE] });
  });
  await uiLogin(page);
  await goTo(page, "Virtual Machines");
  const row = page.getByRole("row", { name: /e2e-ctx-demo/ });
  const box = await row.boundingBox();
  await row.click({ button: "right", position: { x: 40, y: 10 } });
  const menu = page.getByRole("menu", { name: "Actions of e2e-ctx-demo" });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Open", exact: true })).toBeFocused();
  await expect(menu.getByRole("menuitem", { name: "Start", exact: true })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Stop", exact: true })).toHaveCount(0);
  await expect(menu.getByRole("menuitem", { name: /Clone/ })).not.toHaveAttribute("aria-disabled", "true");
  // opened where the pointer was, and inside the window
  const m = await menu.boundingBox();
  const vp = page.viewportSize();
  expect(Math.abs(m.x - (box.x + 40))).toBeLessThan(4);
  expect(m.y).toBeLessThanOrEqual(box.y + 10 + 1); // at the pointer, or moved up to stay inside the window
  expect(m.x + m.width).toBeLessThanOrEqual(vp.width);
  expect(m.y + m.height).toBeLessThanOrEqual(vp.height);
  await expect(row).toHaveClass(/is-ctx/); // the row the actions apply to stays marked
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(row).not.toHaveClass(/is-ctx/);
});

test("right click on a node opens its actions; Open goes to its page", async ({ page }) => {
  await uiLogin(page);
  await goTo(page, "Nodes");
  const row = page.getByRole("main").getByRole("row").nth(1);
  await row.click({ button: "right" });
  const menu = page.getByRole("menu", { name: /^Actions of / });
  await expect(menu.getByRole("menuitem", { name: "Create a VM on this node" })).toBeVisible();
  await menu.getByRole("menuitem", { name: "Open", exact: true }).click();
  await expect(menu).toHaveCount(0);
  await expect(page).toHaveURL(/\/node\//);
});

test("the other lists have their right-click menu too, with the same rules as their buttons", async ({ page }) => {
  await uiLogin(page);
  const main = page.getByRole("main");
  const menuOf = (name: string | RegExp) => page.getByRole("menu", { name: typeof name === "string" ? `Actions of ${name}` : name });

  // Storage: the default pool cannot be removed, and says so
  await goTo(page, "Storage");
  await main.getByRole("row", { name: /default/ }).first().click({ button: "right" });
  await expect(menuOf("default").getByRole("menuitem", { name: "Volumes" })).toBeVisible();
  await expect(menuOf("default").getByRole("menuitem", { name: /^Delete/ })).toHaveAttribute("aria-disabled", "true");
  await expect(menuOf("default")).toContainText("The default pool cannot be removed");
  await page.keyboard.press("Escape");

  // Network: the protected network cannot be deleted
  await goTo(page, "Network");
  await main.getByRole("row", { name: /hyperlite-isolated/ }).first().click({ button: "right" });
  await expect(menuOf("hyperlite-isolated").getByRole("menuitem", { name: /^Delete/ })).toHaveAttribute("aria-disabled", "true");
  await menuOf("hyperlite-isolated").getByRole("menuitem", { name: "Details" }).click();
  await expect(menuOf("hyperlite-isolated")).toHaveCount(0);

  // Users: you cannot delete yourself or change your own role
  await page.goto("/datacenter?tab=permissions");
  await main.getByRole("row", { name: /admin/ }).first().click({ button: "right" });
  const me = menuOf("admin");
  await expect(me.getByRole("menuitem", { name: /^Delete/ })).toHaveAttribute("aria-disabled", "true");
  await expect(me.getByRole("menuitem", { name: "Make observer" })).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Escape");

  // Home: the node table has the node menu
  await page.goto("/datacenter");
  await main.getByRole("row").filter({ has: page.getByRole("button") }).nth(0).click({ button: "right" });
  await expect(page.getByRole("menu", { name: /^Actions of / }).getByRole("menuitem", { name: "Create a VM on this node" })).toBeVisible();
});
