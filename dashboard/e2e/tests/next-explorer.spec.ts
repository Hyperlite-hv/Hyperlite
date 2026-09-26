import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { apiLogin, expect, test, ADMIN, PREFIX } from "../support/fixtures";

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
  await expect(page.getByRole("navigation", { name: "Main navigation" }).locator(".nx-cluster")).toBeVisible();
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
  // These pages need at least one VM (a fresh CI host has none): create one and remove it afterwards.
  const VM = `${PREFIX}expl-${Date.now().toString().slice(-6)}`;
  test.beforeAll(async ({ request }) => {
    const token = await apiLogin(request);
    const res = await request.post("/vms", { headers: { Authorization: `Bearer ${token}` }, data: { name: VM, vcpu: 1, memory_mb: 256, disks: [{ size_gb: 3 }], network: "hyperlite-isolated", username: "tester", password: "Testpass1" } });
    expect(res.ok(), await res.text()).toBe(true);
    await expect.poll(async () => (await request.get(`/vms/${VM}`, { headers: { Authorization: `Bearer ${token}` } })).ok(), { timeout: 90_000 }).toBe(true);
  });
  test.afterAll(async ({ request }) => {
    const h = { Authorization: `Bearer ${await apiLogin(request)}` };
    await request.post(`/vms/${VM}/stop?force=true`, { headers: h }).catch(() => {});
    await request.delete(`/vms/${VM}?confirm=true`, { headers: h }).catch(() => {});
  });

  test("shows the main landmarks and the sidebar without an inventory tree; the cluster button opens the search", async ({ page, problems }) => {
    await nextLogin(page);
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.getByRole("tree")).toHaveCount(0);
    await page.locator(".nx-cluster").click();
    const palette = page.getByRole("dialog", { name: "Find a node or VM" });
    await expect(palette).toBeVisible();
    await palette.getByRole("combobox").fill("Datacenter");
    await expect(palette.getByRole("option").first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();
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

  test("the sidebar can be widened; the width is remembered and can be reset", async ({ page }) => {
    await nextLogin(page);
    const side = page.getByRole("navigation", { name: "Main navigation" });
    const width = async () => Math.round((await side.boundingBox())!.width);
    await page.waitForTimeout(500); // the column width is animated
    const w0 = await width();
    const handle = page.getByRole("separator", { name: /Resize the sidebar/ });
    await handle.focus();
    for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowRight");
    await expect.poll(width).toBeGreaterThan(w0 + 40);
    await page.reload();
    await expect(page.locator(".nx-root")).toBeVisible();
    await expect.poll(width).toBeGreaterThan(w0 + 40); // remembered
    await page.getByRole("separator", { name: /Resize the sidebar/ }).focus();
    await page.keyboard.press("Enter"); // reset
    await expect.poll(width).toBe(w0);
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

  test("selection and tab live in the URL and the browser Back button restores them", async ({ page }) => {
    await nextLogin(page);
    await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Nodes" }).click();
    await page.getByRole("main").getByRole("table").getByRole("button").first().click();
    await expect(page).toHaveURL(/\/node\/local/);
    await page.getByRole("tab", { name: "System summary" }).click();
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

  test("all eight node pages are reachable as flat tabs", async ({ page }) => {
    await nextLogin(page);
    for (const [id, label] of [["summary", "Summary"], ["perf", "Performance"], ["system", "System summary"], ["tasks", "Tasks"], ["network", "Network"], ["disk", "Disk storage"], ["compat", "Compatibility"], ["shell", "Shell"]]) {
      await page.goto(id === "summary" ? "/node/local" : `/node/local?tab=${id}`);
      await expect(page.getByRole("main").getByRole("tab", { name: label, exact: true }), id).toHaveAttribute("aria-selected", "true");
    }
  });

  test("node summary: configuration, host charts, VMs, activity and alerts; direct actions and Actions menu", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/node/local");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: "Configuration" })).toBeVisible();
    await expect(main.getByRole("heading", { name: /^Performance · last hour/ })).toBeVisible();
    for (const h of ["CPU", "Memory"]) await expect(main.getByRole("heading", { level: 3, name: h, exact: true })).toBeVisible();
    await expect(main.getByRole("heading", { name: "Recent activity" })).toBeVisible();
    await expect(main.getByRole("heading", { name: /^Alerts/ })).toBeVisible();
    await main.getByRole("button", { name: "Table" }).click();
    await expect(main.getByRole("table")).toBeVisible();
    await main.getByRole("button", { name: "Cards" }).click();
    await expect(main.getByRole("table")).toHaveCount(0);
    await page.getByRole("button", { name: /^Actions/ }).click();
    await expect(page.getByRole("menuitem", { name: "Copy link" })).toBeVisible();
    await page.keyboard.press("Escape");
    const head = page.locator(".nx-headactions");
    await head.getByRole("button", { name: "New VM", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await head.getByRole("button", { name: "Shell", exact: true }).click();
    await expect(page).toHaveURL(/tab=shell/);
    await main.getByRole("tab", { name: "Performance" }).click();
    await expect(main.getByRole("group", { name: "History range" })).toBeVisible();
  });

  test("the Overview answers: headline figures, host history, nodes, alerts, operations, pools, events", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter");
    const main = page.getByRole("main");
    for (const name of ["CPU history", "Memory history", "Nodes", "Alerts", "Storage pools"]) await expect(main.getByRole("heading", { level: 2, name: new RegExp(`^${name}`) })).toBeVisible();
    await expect(main.getByRole("group", { name: "Inventory" })).toBeVisible();
    await main.getByRole("group", { name: "Inventory" }).getByRole("button", { name: /Virtual machines/i }).click();
    await expect(page).toHaveURL(/tab=vms/);
    // the three views of the overview
    await page.goto("/datacenter");
    await main.getByRole("tab", { name: "Performance" }).click();
    await expect(main.getByRole("group", { name: "History range" })).toBeVisible();
    await main.getByRole("tab", { name: "Events" }).click();
    await expect(main.getByRole("heading", { level: 2, name: /Recent activity/ })).toBeVisible();
    await main.getByRole("tab", { name: "Summary" }).click();
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
    await page.goto("/datacenter?tab=vms");
    const vm = page.getByRole("main").getByRole("button", { name: VM }).first();
    await expect(vm).toBeVisible({ timeout: 30_000 });
    await vm.click();
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: "Configuration" })).toBeVisible();
    await expect(main.getByRole("heading", { name: "Protection" })).toBeVisible();
    await expect(main.getByRole("heading", { name: /^Performance · last hour/ })).toBeVisible();
    await expect(main.getByText("All operations")).toBeVisible();
    for (const tab of ["Summary", "Performance", "Snapshots", "Backups", "Hardware", "Network", "Console"]) await expect(main.getByRole("tab", { name: tab, exact: true })).toBeVisible();
    for (const action of ["Snapshot", "Migrate…"]) await expect(page.locator(".nx-headactions").getByRole("button", { name: new RegExp(`^${action}`) })).toBeVisible();
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
    await page.getByRole("banner").getByRole("button", { name: /^Tasks/ }).click();
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

  test("on a tablet the sidebar opens as a drawer and closes after a navigation", async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1180 });
    await nextLogin(page).catch(() => {});
    const toggle = page.getByRole("button", { name: /sidebar/i }).first();
    await expect(toggle).toBeVisible();
    await toggle.click();
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("button", { name: "Nodes" })).toBeInViewport();
    await nav.getByRole("button", { name: "Nodes" }).click();
    await expect(page).toHaveURL(/tab=nodes/);
    await expect(nav.getByRole("button", { name: "Nodes" })).not.toBeInViewport();
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
