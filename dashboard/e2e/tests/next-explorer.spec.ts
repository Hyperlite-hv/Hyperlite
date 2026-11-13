import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { apiLogin, expect, test, ADMIN, PREFIX, resourceMenu, resourcePage } from "../support/fixtures";

// The console (dashboard/src/next): header band, service menu, Home, service pages and resource pages.
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
  await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
}

// Datacenter-level pages (historical ?tab= ids) and the title of their page.
// "templates" is the historical id of the Library page (ISO images and templates).
const DATACENTER_PAGES: Record<string, string> = {
  summary: "Hyperlite services", activity: "Activity", storage: "Storage", templates: "ISO images and templates", library: "ISO images and templates",
  backups: "Backups", exports: "Exports", permissions: "Users and access", network: "Network", automation: "Automation", containers: "Containers",
  nodes: "Nodes", ha: "High availability", compat: "Compatibility", notifications: "Notifications", sso: "Users and access",
  journal: "Activity", vms: "Virtual machines", snapshots: "Snapshots", metrics: "Metrics", services: "All services", all: "All resources", create: "Create a resource",
};

test.describe("Console: header band, service menu, Home and resource pages", () => {
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

  test("shows the landmarks: header band with search and notifications, service menu, Home", async ({ page, problems }) => {
    await nextLogin(page);
    const banner = page.getByRole("banner");
    await expect(banner.getByRole("button", { name: "Find a node or VM" })).toBeVisible();
    await expect(banner.getByRole("button", { name: /^Running operations/ })).toBeVisible();
    await expect(page.getByRole("tree")).toHaveCount(0);
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    for (const item of ["Create a resource", "Home", "All resources", "All services"]) await expect(nav.getByRole("button", { name: item, exact: true })).toBeVisible();
    await expect(page.getByRole("main").getByRole("heading", { level: 1, name: "Hyperlite services" })).toBeVisible();
    for (const h of ["Resources", "Navigate", "Cluster state", "Needs attention", "Running operations"]) await expect(page.getByRole("main").getByRole("heading", { level: 2, name: new RegExp(`^${h}`) })).toBeVisible();
    expect(problems.filter((p) => !/Failed to load resource|width\(-1\)/.test(p))).toEqual([]);
  });

  test("the notifications pane lists running operations and alerts and closes with Escape", async ({ page }) => {
    await nextLogin(page);
    await page.getByRole("banner").getByRole("button", { name: /^Running operations/ }).click();
    const pane = page.getByRole("dialog", { name: "Notifications" });
    await expect(pane).toBeVisible();
    await expect(pane.getByRole("heading", { name: /^Running operations/ })).toBeVisible();
    await expect(pane.getByRole("heading", { name: /^Alerts/ })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(pane).toBeHidden();
  });

  test("the service menu opens pinned services, marks the current one and can be collapsed", async ({ page }) => {
    await nextLogin(page);
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    for (const [item, tab, title] of [["Storage", "storage", "Storage"], ["Backups", "backups", "Backups"], ["Virtual machines", "vms", "Virtual machines"], ["Home", "summary", "Hyperlite services"]]) {
      await nav.getByRole("button", { name: item, exact: true }).click();
      await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toHaveText(title);
      if (tab !== "summary") await expect(page).toHaveURL(new RegExp(`tab=${tab}`));
      await expect(nav.getByRole("button", { name: item, exact: true })).toHaveAttribute("aria-current", "page");
    }
    const width = async () => Math.round((await nav.boundingBox())!.width);
    const w0 = await width();
    await nav.getByRole("button", { name: "Collapse the menu" }).click();
    await expect.poll(width).toBeLessThan(w0 / 2);
    await nav.getByRole("button", { name: "Expand the menu" }).click();
    await expect.poll(width).toBe(w0);
  });

  test("All services lists every service by family and a star pins one in the menu", async ({ page }) => {
    await nextLogin(page);
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await nav.getByRole("button", { name: "All services", exact: true }).click();
    const main = page.getByRole("main");
    for (const family of ["Compute", "Storage", "Network", "Cluster", "Protection", "Monitoring", "Automation"]) await expect(main.getByRole("heading", { level: 2, name: family, exact: true })).toBeVisible();
    await expect(nav.getByRole("button", { name: "High availability", exact: true })).toHaveCount(0);
    await main.getByRole("button", { name: "Pin High availability in the menu" }).click();
    await expect(nav.getByRole("button", { name: "High availability", exact: true })).toBeVisible();
    await page.reload();
    await expect(nav.getByRole("button", { name: "High availability", exact: true })).toBeVisible(); // remembered
    await page.getByRole("main").getByRole("button", { name: "Unpin High availability from the menu" }).click();
    await expect(nav.getByRole("button", { name: "High availability", exact: true })).toHaveCount(0);
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

  test("selection and page live in the URL and the browser Back button restores them", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter?tab=nodes");
    await page.getByRole("main").getByRole("table").getByRole("button").first().click();
    await expect(page).toHaveURL(/\/node\/local/);
    await resourcePage(page, "System").click();
    await expect(page).toHaveURL(/tab=system/);
    await expect(resourcePage(page, "System")).toHaveAttribute("aria-current", "page");
    await page.goBack();
    await expect(page).toHaveURL(/\/node\/local$/);
    await expect(resourcePage(page, "Overview")).toHaveAttribute("aria-current", "page");
  });

  test("every historical Datacenter ?tab= deep link still opens its page", async ({ page }) => {
    await nextLogin(page);
    for (const [id, title] of Object.entries(DATACENTER_PAGES)) {
      await page.goto(id === "summary" ? "/datacenter" : `/datacenter?tab=${id}`);
      await expect(page.getByRole("main").getByRole("heading", { level: 1 }), id).toHaveText(title);
    }
  });

  test("every node page is in the node's menu and opens from its historical ?tab=", async ({ page }) => {
    await nextLogin(page);
    for (const [id, label] of [["summary", "Overview"], ["perf", "Performance"], ["system", "System"], ["updates", "Updates"], ["network", "Network"], ["disk", "Storage"], ["tasks", "Tasks"], ["compat", "Compatibility"], ["shell", "Shell"]]) {
      await page.goto(id === "summary" ? "/node/local" : `/node/local?tab=${id}`);
      await expect(resourcePage(page, label), id).toHaveAttribute("aria-current", "page");
    }
  });

  test("node overview: Essentials, properties, its machines, monitoring; commands in the bar, the rest under More", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/node/local");
    const main = page.getByRole("main");
    await expect(main.getByRole("region", { name: "Essentials" })).toBeVisible();
    await expect(main.getByRole("heading", { level: 2, name: /^Virtual machines on this node/ })).toBeVisible();
    await expect(main.getByRole("heading", { level: 2, name: "Recent activity" })).toBeVisible();
    const bar = page.locator(".nx-oh-acts");
    for (const cmd of ["Create a VM", "Shell", "Refresh"]) await expect(bar.getByRole("button", { name: cmd, exact: true })).toBeVisible();
    await bar.getByRole("button", { name: /^More/ }).click();
    await expect(page.getByRole("menuitem", { name: "Copy link" })).toBeVisible();
    await page.getByRole("menuitem", { name: "Open the shell" }).click();
    await expect(page).toHaveURL(/tab=shell/);
    await page.goto("/node/local");
    await main.getByRole("tab", { name: "Monitoring" }).click();
    await expect(main.getByRole("group", { name: "History range" })).toBeVisible();
  });

  test("the Virtual machines page: commands, filters read as sentences, grouping by node, sortable columns", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter?tab=vms");
    const main = page.getByRole("main");
    const bar = main.getByRole("toolbar");
    for (const cmd of ["Create", "Refresh", "Export to CSV"]) await expect(bar.getByRole("button", { name: cmd, exact: true })).toBeVisible();
    // The commands on a selection wait for one.
    await expect(bar.getByRole("button", { name: /^Start/ })).toHaveAttribute("aria-disabled", "true");
    await main.getByRole("combobox", { name: "State equals" }).selectOption("running");
    await main.getByRole("combobox", { name: "State equals" }).selectOption("");
    await main.getByRole("combobox", { name: "Group by" }).selectOption("node");
    await expect(main.getByRole("columnheader", { name: /^Name/ })).toBeVisible();
    const nameHeader = main.getByRole("columnheader", { name: /Name/ });
    await nameHeader.getByRole("button").click();
    await expect(nameHeader).toHaveAttribute("aria-sort", /ascending|descending/);
    await main.getByRole("checkbox", { name: new RegExp(VM) }).check();
    await expect(bar.getByRole("button", { name: /^Start/ })).not.toHaveAttribute("aria-disabled", "true");
  });

  test("the Tasks page filters tasks by status and period", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter?tab=activity");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { level: 1, name: /^Activity/ })).toBeVisible();
    await expect(main.getByRole("tab", { name: "Tasks", selected: true })).toBeVisible();
    const filters = main.getByRole("group", { name: "Task filters" });
    await main.getByRole("group", { name: "Status" }).getByRole("button", { name: /^Failed/ }).click();
    await filters.getByLabel("Period").selectOption("all");
    await expect(main.getByRole("button", { name: "Export as CSV" })).toBeVisible();
  });

  test("a VM page: Essentials, properties by subject, every page in its menu, commands and More", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter?tab=vms");
    const vm = page.getByRole("main").getByRole("button", { name: VM, exact: true }).first();
    await expect(vm).toBeVisible({ timeout: 30_000 });
    await vm.click();
    const main = page.getByRole("main");
    await expect(main.getByRole("region", { name: "Essentials" })).toBeVisible();
    for (const h of ["Virtual machine", "Networking", "Availability and placement", "Size", "Disks", "Backup and snapshots"]) await expect(main.getByRole("heading", { level: 2, name: h, exact: true })).toBeVisible();
    for (const p of ["Overview", "Tasks", "Console", "Hardware", "Advanced", "Options and limits", "Network", "Cloud-init", "Snapshots", "Backups", "Performance"]) await expect(resourcePage(page, p)).toBeVisible();
    const bar = page.locator(".nx-oh-acts");
    for (const cmd of ["Connect", "Start", "Restart", "Stop", "Refresh"]) await expect(bar.getByRole("button", { name: new RegExp(`^${cmd}`) })).toBeVisible();
    await bar.getByRole("button", { name: /^More/ }).click();
    const menu = page.getByRole("menu");
    for (const g of ["Power", "Protection", "Lifecycle"]) await expect(menu.getByText(g, { exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await main.getByRole("button", { name: "JSON view" }).click();
    await expect(page.getByRole("dialog", { name: "JSON view" })).toContainText(VM);
  });

  test("language and theme switches from the account menu apply immediately and persist", async ({ page }) => {
    await nextLogin(page);
    await page.locator(".nx-rail-user").click();
    await page.getByRole("menuitem", { name: "Français" }).click();
    await expect(page.getByRole("navigation", { name: "Navigation principale" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("navigation", { name: "Navigation principale" })).toBeVisible();
    await page.locator(".nx-rail-user").click();
    await page.getByRole("menuitem", { name: "Clair" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  });

  test("on a tablet the menu opens as a drawer and closes after a navigation", async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1180 });
    await nextLogin(page).catch(() => {});
    const toggle = page.getByRole("button", { name: "Toggle sidebar" });
    await expect(toggle).toBeVisible();
    await toggle.click();
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("button", { name: "Nodes", exact: true })).toBeInViewport();
    await nav.getByRole("button", { name: "Nodes", exact: true }).click();
    await expect(page).toHaveURL(/tab=nodes/);
    await expect(nav.getByRole("button", { name: "Nodes", exact: true })).not.toBeInViewport();
  });

  test("on a phone nothing overflows horizontally and search stays reachable", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => { localStorage.setItem("hyperlite-ui", "next"); });
    await page.goto("/");
    await page.getByLabel("Username").fill(ADMIN.username);
    await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
    for (const path of ["/datacenter", "/datacenter?tab=vms", "/node/local"]) {
      await page.goto(path);
      await expect(page.locator(".nx-root")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), path).toBe(false);
    }
    await expect(page.getByRole("button", { name: "Find a node or VM" })).toBeVisible();
    await expect(resourceMenu(page)).toBeVisible();
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
