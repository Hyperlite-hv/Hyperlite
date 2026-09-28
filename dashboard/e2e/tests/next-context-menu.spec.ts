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
