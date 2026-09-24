import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, test, ADMIN } from "../support/fixtures";

// Rebuilt interface (dashboard/src/next): shell, Inventory Explorer, navigation parity.
async function nextLogin(page: Page, { theme = "dark", lang = "en" } = {}) {
  await page.addInitScript(([th, lg]) => {
    // Seeded once: reloads inside a test must keep the user's own choices.
    if (!localStorage.getItem("hyperlite-ui")) {
      localStorage.setItem("hyperlite-ui", "next");
      localStorage.setItem("hyperlite-next-theme", th);
      localStorage.setItem("hyperlite-next-lang", lg);
    }
  }, [theme, lang]);
  await page.goto("/");
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("tree").getByRole("treeitem").first()).toBeVisible();
}

const DATACENTER_TABS: Record<string, string> = {
  summary: "Summary", activity: "Recent activity", storage: "Storage", templates: "Templates", backups: "Backups", exports: "Exports",
  permissions: "Users and access", reseau: "Network", automation: "Automation", containers: "Containers", nodes: "Nodes", ha: "HA",
  compat: "Compatibility", notifications: "Notifications", sso: "SSO", journal: "Journal",
};
const NODE_TABS = ["Summary", "System summary", "Network", "Disk storage", "Tasks", "Compatibility", "Shell"];

test.describe("Rebuilt interface: shell and Inventory Explorer", () => {
  test("shows landmarks, the skip link and the hierarchical inventory", async ({ page, problems }) => {
    await nextLogin(page);
    await expect(page.getByRole("banner")).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Inventory" })).toBeVisible();
    await expect(page.getByRole("main")).toBeVisible();
    const tree = page.getByRole("tree", { name: "Inventory tree" });
    await expect(tree.getByRole("treeitem").first()).toHaveAttribute("aria-level", "1");
    await expect(tree.getByRole("treeitem", { name: /Datacenter/ })).toBeVisible();
    await expect(tree.getByRole("treeitem", { name: /node, / })).toBeVisible();
    expect(problems.filter((p) => !/Failed to load resource/.test(p))).toEqual([]);
  });

  test("arrow keys, Home/End, Right/Left and Enter work in the tree (single tab stop)", async ({ page }) => {
    await nextLogin(page);
    const tree = page.getByRole("tree");
    const items = tree.getByRole("treeitem");
    await items.first().focus();
    const label = () => page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
    const first = await label();
    await page.keyboard.press("ArrowDown");
    expect(await label()).not.toBe(first);
    await page.keyboard.press("End");
    const last = await label();
    await page.keyboard.press("Home");
    expect(await label()).toBe(first);
    expect(last).not.toBe(first);
    // exactly one treeitem is in the tab order
    await expect(tree.locator('[role="treeitem"][tabindex="0"]')).toHaveCount(1);
    // collapse / expand the Datacenter root
    await page.keyboard.press("ArrowLeft");
    await expect(items.first()).toHaveAttribute("aria-expanded", "false");
    await page.keyboard.press("ArrowRight");
    await expect(items.first()).toHaveAttribute("aria-expanded", "true");
    // Enter on the node opens it
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/node\//);
  });

  test("clicking a row selects it without collapsing its branch; the chevron toggles", async ({ page }) => {
    await nextLogin(page);
    const dc = page.getByRole("treeitem", { name: /Datacenter/ });
    await dc.click();
    await expect(dc).toHaveAttribute("aria-expanded", "true");
    await dc.locator(".nx-chev").click();
    await expect(dc).toHaveAttribute("aria-expanded", "false");
  });

  test("search filters with a live result count, highlights, and explains an empty result", async ({ page }) => {
    await nextLogin(page);
    const box = page.getByRole("searchbox");
    await box.fill("stor");
    await expect(page.locator(".nx-results")).toContainText(/result/);
    await expect(page.locator("mark.nx-hit").first()).toBeVisible();
    await box.fill("zzzzqq");
    await expect(page.getByText("No node or VM matches “zzzzqq”.")).toBeVisible();
    await page.getByRole("button", { name: "Clear search" }).click();
    await expect(box).toHaveValue("");
    await expect(page.getByRole("treeitem", { name: /Datacenter/ })).toBeVisible();
    await box.fill("x");
    await box.press("Escape");
    await expect(box).toHaveValue("");
  });

  test("the search shortcut / focuses the box and Ctrl+K opens the command palette", async ({ page }) => {
    await nextLogin(page);
    await page.locator("main").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("/");
    await expect(page.getByRole("searchbox")).toBeFocused();
    await page.getByRole("searchbox").blur();
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Find a node or VM" });
    await expect(palette).toBeVisible();
    await palette.getByRole("combobox").fill("Datacenter");
    await expect(palette.getByRole("option").first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();
  });

  test("Server and Pool modes; the choice survives a reload", async ({ page }) => {
    await nextLogin(page);
    await page.getByRole("button", { name: "Pool" }).click();
    await expect(page.getByRole("button", { name: "Pool" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("treeitem", { name: /Unassigned/ })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Pool" })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Server" }).click();
    await expect(page.getByRole("treeitem", { name: /node, / })).toBeVisible();
  });

  test("selection and tab live in the URL and the browser Back button restores them", async ({ page }) => {
    await nextLogin(page);
    await page.getByRole("treeitem", { name: /node, / }).click();
    await expect(page).toHaveURL(/\/node\/local/);
    await page.getByRole("tab", { name: "Monitor" }).click();
    await expect(page).toHaveURL(/tab=system/);
    await page.goBack();
    await expect(page).toHaveURL(/\/node\/local$/);
    await expect(page.getByRole("tab", { name: "Summary", exact: true })).toHaveAttribute("aria-selected", "true");
    await page.goBack();
    await expect(page).toHaveURL(/\/datacenter/);
  });

  test("every historical Datacenter ?tab= deep link still opens its tab", async ({ page }) => {
    await nextLogin(page);
    for (const [id, label] of Object.entries(DATACENTER_TABS)) {
      await page.goto(id === "summary" ? "/datacenter" : `/datacenter?tab=${id}`);
      await expect(page.getByRole("tab", { name: label, exact: true }), id).toHaveAttribute("aria-selected", "true");
    }
  });

  test("all seven node pages are reachable (Summary, Monitor > 2, Configure > 4)", async ({ page }) => {
    await nextLogin(page);
    for (const [id, label] of [["summary", "Summary"], ["system", "System summary"], ["tasks", "Tasks"], ["network", "Network"], ["disk", "Disk storage"], ["compat", "Compatibility"], ["shell", "Shell"]]) {
      await page.goto(id === "summary" ? "/node/local" : `/node/local?tab=${id}`);
      await expect(page.getByRole("main").getByRole("tab", { name: label, exact: true }), id).toHaveAttribute("aria-selected", "true");
    }
  });

  test("Datacenter has vSphere-style tabs; Configure lists its pages in a grouped vertical menu", async ({ page }) => {
    await nextLogin(page);
    const top = page.getByRole("tablist", { name: "Datacenter" });
    for (const name of ["Summary", "Monitor", "Configure", "Permissions", "Containers"]) await expect(top.getByRole("tab", { name })).toBeVisible();
    await top.getByRole("tab", { name: "Configure" }).click();
    const menu = page.getByRole("tablist", { name: "Section pages" });
    await expect(menu.getByRole("tab", { name: "Nodes" })).toHaveAttribute("aria-selected", "true");
    await menu.getByRole("tab", { name: "Storage" }).click();
    await expect(page).toHaveURL(/tab=storage/);
    await expect(page.getByRole("heading", { name: "Storage", level: 2 })).toBeVisible();
    await menu.getByRole("tab", { name: "Storage" }).press("ArrowDown");
    await expect(page).toHaveURL(/tab=reseau/);
    await top.getByRole("tab", { name: "Monitor" }).click();
    await expect(page).toHaveURL(/tab=activity/);
  });

  test("inventory views (hosts, VMs, storage, networks) and the Actions menu", async ({ page }) => {
    await nextLogin(page);
    const views = page.getByRole("tablist", { name: "Inventory view" });
    for (const [name, expected] of [["VMs and containers", /Virtual machines/], ["Storage pools", /node, /], ["Virtual networks", /Datacenter/], ["Hosts and servers", /node, /]] as const) {
      await views.getByRole("tab", { name }).click();
      await expect(views.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("tree").getByRole("treeitem", { name: expected }).first()).toBeVisible();
    }
    await page.getByRole("button", { name: /^Actions/ }).click();
    await expect(page.getByRole("menuitem", { name: "Copy link" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: /Create · Virtual machine/ })).toBeVisible();
    await page.keyboard.press("Escape");
  });

  test("language and theme switches apply immediately and persist", async ({ page }) => {
    await nextLogin(page);
    await page.getByRole("button", { name: "Language" }).click();
    await page.getByRole("menuitem", { name: "Français" }).click();
    await expect(page.getByRole("tab", { name: "Configurer" })).toBeVisible();
    await expect(page.getByPlaceholder("Trouver un nœud ou une VM")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("tab", { name: "Configurer" })).toBeVisible();
    await page.getByRole("button", { name: "Thème" }).click();
    await page.getByRole("menuitem", { name: "Clair" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  });

  test("a health badge is always visible and opens the alerts; node pages show a breadcrumb", async ({ page }) => {
    await nextLogin(page);
    const badge = page.getByRole("button", { name: /Infrastructure health/ });
    await expect(badge).toBeVisible();
    await badge.click();
    await expect(page.getByRole("tab", { name: /^Alerts/ })).toHaveAttribute("aria-selected", "true");
    await page.goto("/node/local");
    const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
    await expect(crumbs.getByRole("button", { name: "Datacenter" })).toBeVisible();
    await crumbs.getByRole("button", { name: "Datacenter" }).click();
    await expect(page).toHaveURL(/\/datacenter/);
  });

  test("the task dock starts collapsed and opens on demand", async ({ page }) => {
    await nextLogin(page);
    const dock = page.getByRole("region", { name: "Tasks" }).first();
    await expect(page.getByRole("button", { name: "Expand tasks panel" })).toBeVisible();
    await page.getByRole("button", { name: "Expand tasks panel" }).click();
    await expect(page.getByText("No tasks yet.")).toBeVisible();
    await expect(dock).toBeVisible();
  });

  test("on a tablet the inventory opens as a drawer from a button that is always present", async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1180 });
    await nextLogin(page);
    const open = page.getByRole("button", { name: "Inventory", exact: true });
    await expect(open).toBeVisible();
    await open.click();
    await expect(page.getByRole("tree")).toBeVisible();
    await page.getByRole("treeitem", { name: /node, / }).click();
    await expect(page).toHaveURL(/\/node\//);
  });

  test("on a phone nothing overflows horizontally and search stays reachable", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await nextLogin(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
    await expect(page.getByRole("button", { name: "Find a node or VM" })).toBeVisible();
  });

  for (const theme of ["dark", "light"]) {
    test(`no serious or critical axe violations on the shell (${theme})`, async ({ page }) => {
      await nextLogin(page, { theme });
      await page.waitForLoadState("networkidle");
      const results = await new AxeBuilder({ page }).include(".nx-root").withTags(["wcag2a", "wcag2aa"]).analyze();
      const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(blocking.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
    });
  }
});
