import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, test, ADMIN } from "../support/fixtures";

// Rebuilt interface (dashboard/src/next): sidebar navigation, inventory, object pages.
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

// Datacenter-level pages (historical ?tab= ids) and the title of their page.
const DATACENTER_PAGES: Record<string, string> = {
  summary: "Overview", activity: "Recent activity", storage: "Storage", templates: "Templates", backups: "Backups", exports: "Exports",
  permissions: "Permissions", reseau: "Network", automation: "Automation", containers: "Containers", nodes: "Nodes", ha: "HA",
  compat: "Compatibility", notifications: "Notifications", sso: "SSO", journal: "Journal", vms: "Virtual machines", snapshots: "Snapshots",
};


// Navigation groups other than Infrastructure and Management start closed: open every group first.
async function openNavGroups(page: Page) {
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  for (const g of ["Observability", "Administration", "More"]) {
    const b = nav.getByRole("button", { name: g, exact: true });
    if ((await b.count()) && (await b.getAttribute("aria-expanded")) === "false") await b.click();
  }
}

test.describe("Rebuilt interface: sidebar, inventory and object pages", () => {
  test("shows the main landmarks, the sidebar navigation and the hierarchical inventory", async ({ page, problems }) => {
    await nextLogin(page);
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    await expect(page.getByRole("main")).toBeVisible();
    const tree = page.getByRole("tree", { name: "Inventory tree" });
    await expect(tree.getByRole("treeitem").first()).toHaveAttribute("aria-level", "1");
    await expect(tree.getByRole("treeitem", { name: /Datacenter/ })).toBeVisible();
    await expect(tree.getByRole("treeitem", { name: /node, / })).toBeVisible();
    expect(problems.filter((p) => !/Failed to load resource|width\(-1\)/.test(p))).toEqual([]);
  });

  test("the sidebar reaches every Datacenter page and marks the current one", async ({ page }) => {
    await nextLogin(page);
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await openNavGroups(page);
    for (const [item, tab, title] of [["Storage", "storage", "Storage"], ["Backups", "backups", "Backups"], ["Virtual Machines", "vms", "Virtual machines"], ["Users & Roles", "permissions", "Permissions"], ["Overview", "summary", "Overview"]] as const) {
      await nav.getByRole("button", { name: item }).click();
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);
      if (tab !== "summary") await expect(page).toHaveURL(new RegExp(`tab=${tab}`));
      await expect(nav.getByRole("button", { name: item })).toHaveAttribute("aria-current", "page");
    }
  });

  test("the sidebar can be widened and its navigation area resized; both are remembered and can be reset", async ({ page }) => {
    await nextLogin(page);
    const side = page.getByRole("navigation", { name: "Main navigation" });
    const width = async () => Math.round((await side.boundingBox())!.width);
    await page.waitForTimeout(500); // the column width is animated
    const w0 = await width();
    const handle = page.getByRole("separator", { name: /Resize the sidebar/ });
    await handle.focus();
    for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowRight");
    await expect.poll(width).toBeGreaterThan(w0 + 40);
    const split = page.getByRole("separator", { name: /Resize the navigation area/ });
    const navBox = page.locator(".nx-nav-scroll");
    const h0 = Math.round((await navBox.boundingBox())!.height);
    await split.focus();
    for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowUp");
    expect(Math.round((await navBox.boundingBox())!.height)).toBeGreaterThan(h0 + 30);
    await page.reload();
    await expect(page.locator(".nx-root")).toBeVisible();
    await expect.poll(width).toBeGreaterThan(w0 + 40); // remembered
    await page.getByRole("separator", { name: /Resize the sidebar/ }).focus();
    await page.keyboard.press("Enter"); // reset
    await expect.poll(width).toBe(w0);
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
    await expect(tree.locator('[role="treeitem"][tabindex="0"]')).toHaveCount(1);
    await page.keyboard.press("ArrowLeft");
    await expect(items.first()).toHaveAttribute("aria-expanded", "false");
    await page.keyboard.press("ArrowRight");
    await expect(items.first()).toHaveAttribute("aria-expanded", "true");
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
    await box.fill("x");
    await box.press("Escape");
    await expect(box).toHaveValue("");
  });

  test("Ctrl+K and / open the command palette, which finds resources", async ({ page }) => {
    await nextLogin(page);
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Find a node or VM" });
    await expect(palette).toBeVisible();
    await palette.getByRole("combobox").fill("Datacenter");
    await expect(palette.getByRole("option").first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();
    await page.locator("main").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("/");
    await expect(palette).toBeVisible();
  });

  test("Server and Pool modes; the choice survives a reload", async ({ page }) => {
    await nextLogin(page);
    await page.getByRole("button", { name: "Pool", exact: true }).click();
    await expect(page.getByRole("button", { name: "Pool", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("treeitem", { name: /Unassigned/ })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Pool", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Server", exact: true }).click();
    await expect(page.getByRole("treeitem", { name: /node, / })).toBeVisible();
  });

  test("inventory views (hosts, VMs, storage pools, networks)", async ({ page }) => {
    await nextLogin(page);
    const views = page.getByRole("tablist", { name: "Inventory view" });
    for (const [name, expected] of [["VMs and containers", /Virtual machines/], ["Storage pools", /node, /], ["Virtual networks", /Datacenter/], ["Hosts and servers", /node, /]] as const) {
      await views.getByRole("tab", { name }).click();
      await expect(views.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("tree").getByRole("treeitem", { name: expected }).first()).toBeVisible();
    }
  });

  test("the inventory group can be collapsed and the choice persists", async ({ page }) => {
    await nextLogin(page);
    const toggle = page.getByRole("button", { name: /Inventory/ }).first();
    await toggle.click();
    await expect(page.getByRole("tree")).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("tree")).toHaveCount(0);
    await page.getByRole("button", { name: /Inventory/ }).first().click();
    await expect(page.getByRole("tree")).toBeVisible();
  });

  test("selection and tab live in the URL and the browser Back button restores them", async ({ page }) => {
    await nextLogin(page);
    await page.getByRole("treeitem", { name: /node, / }).click();
    await expect(page).toHaveURL(/\/node\/local/);
    await page.getByRole("tab", { name: "Monitor" }).click();
    await expect(page).toHaveURL(/tab=system/);
    await page.goBack();
    await expect(page).toHaveURL(/\/node\/local$/);
    await expect(page.getByRole("main").getByRole("tab", { name: "Summary", exact: true })).toHaveAttribute("aria-selected", "true");
  });

  test("every historical Datacenter ?tab= deep link still opens its page", async ({ page }) => {
    await nextLogin(page);
    for (const [id, title] of Object.entries(DATACENTER_PAGES)) {
      await page.goto(id === "summary" ? "/datacenter" : `/datacenter?tab=${id}`);
      await expect(page.getByRole("heading", { level: 1 }), id).toHaveText(title);
    }
  });

  test("all seven node pages are reachable (Summary, Monitor > 2, Configure > 4)", async ({ page }) => {
    await nextLogin(page);
    for (const [id, label] of [["summary", "Summary"], ["system", "System summary"], ["tasks", "Tasks"], ["network", "Network"], ["disk", "Disk storage"], ["compat", "Compatibility"], ["shell", "Shell"]]) {
      await page.goto(id === "summary" ? "/node/local" : `/node/local?tab=${id}`);
      await expect(page.getByRole("main").getByRole("tab", { name: label, exact: true }), id).toHaveAttribute("aria-selected", "true");
    }
  });

  test("node summary: capacity with trends, VMs, health, activity and alerts; Actions menu", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/node/local");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: "Node health" })).toBeVisible();
    await expect(main.getByRole("heading", { name: "Recent activity" })).toBeVisible();
    await expect(main.getByRole("heading", { name: /^Alerts/ })).toBeVisible();
    await main.getByRole("button", { name: "Table" }).click();
    await expect(main.getByRole("table")).toBeVisible();
    await main.getByRole("button", { name: "Cards" }).click();
    await expect(main.getByRole("table")).toHaveCount(0);
    await page.getByRole("button", { name: /^Actions/ }).click();
    await expect(page.getByRole("menuitem", { name: "Copy link" })).toBeVisible();
    await page.keyboard.press("Escape");
  });

  test("the Overview answers: what needs attention, capacity, nodes, pools, recent activity", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter");
    const main = page.getByRole("main");
    for (const name of ["Needs attention", "Nodes", "Storage pools", "Recent activity"]) await expect(main.getByRole("heading", { name: new RegExp(`^${name}`) })).toBeVisible();
    await expect(main.getByRole("group", { name: "Inventory" })).toBeVisible();
    await main.getByRole("group", { name: "Inventory" }).getByRole("button", { name: /Virtual Machines/i }).click();
    await expect(page).toHaveURL(/tab=vms/);
    await page.goto("/datacenter");
    await main.getByRole("table").getByRole("button").first().click();
    await expect(page).toHaveURL(/\/node\//);
  });

  test("the Virtual machines page filters by state and sorts its table", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter?tab=vms");
    const main = page.getByRole("main");
    await main.getByRole("button", { name: "Table" }).click();
    await expect(main.getByRole("group", { name: "Filter by state" })).toBeVisible();
    for (const chip of ["All", "Running", "Stopped", "To check"]) await main.getByRole("button", { name: new RegExp(`^${chip}`) }).click();
    await main.getByRole("button", { name: /^All/ }).click();
    const nameHeader = main.getByRole("columnheader", { name: /Name/ });
    await nameHeader.getByRole("button").click();
    await expect(nameHeader).toHaveAttribute("aria-sort", /ascending|descending/);
  });

  test("the Activity page filters tasks and the sidebar groups collapse", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter?tab=activity");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: /^Tasks/ })).toBeVisible();
    const filters = main.getByRole("group", { name: "Task filters" });
    await filters.getByLabel("Status").selectOption("echec");
    await filters.getByLabel("Period").selectOption("all");
    await expect(main.getByRole("button", { name: "Export CSV" })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("button", { name: "Exports" })).toHaveCount(0);
    await nav.getByRole("button", { name: /^▸ More|More/ }).first().click();
    await expect(nav.getByRole("button", { name: "Exports" })).toBeVisible();
  });

  test("a VM page shows state, capacity, identity, protection and every historical operation", async ({ page }) => {
    await nextLogin(page);
    const vm = page.getByRole("tree").getByRole("treeitem", { name: /virtual machine, / }).first();
    test.skip(!(await vm.count()), "no VM on this host");
    await vm.click();
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: "Identity" })).toBeVisible();
    await expect(main.getByRole("heading", { name: "Protection" })).toBeVisible();
    await expect(main.getByText("All operations")).toBeVisible();
    for (const tab of ["Summary", "Console", "Configure", "Snapshots", "Backup"]) await expect(main.getByRole("tab", { name: tab, exact: true })).toBeVisible();
    await page.getByRole("button", { name: /^Actions/ }).click();
    await expect(page.getByRole("menuitem", { name: /Force stop/ })).toBeVisible();
  });

  test("language and theme switches from the user menu apply immediately and persist", async ({ page }) => {
    await nextLogin(page);
    await page.locator(".nx-sidebar-user").click();
    await page.getByRole("menuitem", { name: "Français" }).click();
    await expect(page.getByRole("navigation", { name: "Navigation principale" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("navigation", { name: "Navigation principale" })).toBeVisible();
    await page.locator(".nx-sidebar-user").click();
    await page.getByRole("menuitem", { name: "Clair" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  });

  test("the activity panel is closed by default, opens from the top bar and closes with Escape", async ({ page }) => {
    await nextLogin(page);
    const panel = page.getByRole("complementary", { name: "Activity" });
    await expect(panel).toBeHidden();
    await page.getByRole("button", { name: /^Tasks/ }).click();
    await expect(panel).toBeVisible();
    await expect(panel.getByText("No tasks yet.")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    await openNavGroups(page);
    await page.getByRole("button", { name: /^Alerts/ }).first().click();
    await expect(panel.getByRole("tab", { name: /^Alerts/ })).toHaveAttribute("aria-selected", "true");
    await panel.getByRole("button", { name: "Close activity panel" }).click();
    await expect(panel).toBeHidden();
  });

  test("on a tablet the sidebar (with the inventory) opens as a drawer", async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1180 });
    await nextLogin(page).catch(() => {});
    const toggle = page.getByRole("button", { name: /sidebar/i }).first();
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(page.getByRole("tree")).toBeVisible();
    await page.getByRole("treeitem", { name: /node, / }).click();
    await expect(page).toHaveURL(/\/node\//);
  });

  test("on a phone nothing overflows horizontally and search stays reachable", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => { localStorage.setItem("hyperlite-ui", "next"); });
    await page.goto("/");
    await page.getByLabel("Username").fill(ADMIN.username);
    await page.getByLabel("Password").fill(ADMIN.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
    await expect(page.getByRole("button", { name: "Find a node or VM" })).toBeVisible();
  });

  for (const theme of ["dark", "light"]) {
    test(`no serious or critical axe violations (${theme})`, async ({ page }) => {
      await nextLogin(page, { theme });
      await page.waitForLoadState("networkidle");
      const results = await new AxeBuilder({ page }).include(".nx-root").withTags(["wcag2a", "wcag2aa"]).analyze();
      const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(blocking.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
    });
  }
});
