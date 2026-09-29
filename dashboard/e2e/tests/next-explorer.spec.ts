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
  await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("navigation", { name: "Main navigation" }).locator(".nx-cluster")).toBeVisible();
}

// Datacenter-level pages (historical ?tab= ids) and the title of their page.
// "templates" is the historical id of the Library page (ISO images and templates).
const DATACENTER_PAGES: Record<string, string> = {
  summary: "Home", activity: "Tasks", storage: "Storage", templates: "ISO images and templates", library: "ISO images and templates",
  backups: "Backups", exports: "Exports", permissions: "Users and roles", reseau: "Network", automation: "Automation", containers: "Containers",
  nodes: "Nodes", ha: "High availability", compat: "Compatibility", notifications: "Notifications", sso: "Authentication (SSO)",
  journal: "Audit log", vms: "Virtual machines", snapshots: "Snapshots",
};

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
    // The sidebar groups are always open (Infrastructure, Cluster, Protection, Library, Operations, Administration).
    for (const g of ["Infrastructure", "Cluster", "Protection", "Library", "Operations", "Administration"]) await expect(nav.getByRole("group", { name: g, exact: true })).toBeVisible();
    for (const [item, tab, title] of [["Storage", "storage", "Storage"], ["Backups", "backups", "Backups"], ["Virtual Machines", "vms", "Virtual machines"], ["Users and roles", "permissions", "Users and roles"], ["Authentication (SSO)", "sso", "Authentication (SSO)"], ["Audit log", "journal", "Audit log"], ["Home", "summary", "Home"]] as const) {
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
    await page.getByRole("tab", { name: "System", exact: true }).click();
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
    for (const [id, label] of [["summary", "Summary"], ["perf", "Performance"], ["system", "System"], ["network", "Network"], ["disk", "Storage"], ["tasks", "Tasks"], ["compat", "Compatibility"], ["shell", "Shell"]]) {
      await page.goto(id === "summary" ? "/node/local" : `/node/local?tab=${id}`);
      await expect(page.getByRole("main").getByRole("tab", { name: label, exact: true }), id).toHaveAttribute("aria-selected", "true");
    }
  });

  test("node summary: KPI strip, VMs on the node, configuration with the alert state, activity; Actions menu", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/node/local");
    const main = page.getByRole("main");
    await expect(main.getByRole("group", { name: "Node resources" })).toBeVisible();
    for (const k of ["CPU", "Memory", "Storage"]) await expect(main.getByRole("group", { name: "Node resources" }).getByText(k, { exact: true })).toBeVisible();
    await expect(main.getByRole("heading", { name: "Virtual machines on this node" })).toBeVisible();
    await expect(main.getByRole("heading", { name: "Configuration" })).toBeVisible();
    await expect(main.getByText("Alerts", { exact: true })).toBeVisible();
    await expect(main.getByRole("heading", { name: "Recent activity" })).toBeVisible();
    // The full charts live only in the Performance tab (R5).
    await expect(main.getByRole("heading", { name: /^Performance · last hour/ })).toHaveCount(0);
    // R3 / R4: "New VM" and "Shell" left the header for the Actions menu.
    const head = page.locator(".nx-oh-acts");
    await expect(head.getByRole("button", { name: "New VM", exact: true })).toHaveCount(0);
    await expect(head.getByRole("button", { name: "Shell", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: /^Actions/ }).click();
    await expect(page.getByRole("menuitem", { name: "Copy link" })).toBeVisible();
    await page.getByRole("menuitem", { name: "Create a VM on this node" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: /^Actions/ }).click();
    await page.getByRole("menuitem", { name: "Open the shell" }).click();
    await expect(page).toHaveURL(/tab=shell/);
    await main.getByRole("tab", { name: "Performance" }).click();
    await expect(main.getByRole("group", { name: "History range" })).toBeVisible();
  });

  test("the Overview answers: headline figures, nodes, watch list, pools, activity; charts only in Performance", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter");
    const main = page.getByRole("main");
    for (const name of ["Nodes", "To watch", "Storage pools", "Recent activity"]) await expect(main.getByRole("heading", { level: 2, name: new RegExp(`^${name}`) })).toBeVisible();
    // R13: no chart on the summary view.
    await expect(main.getByRole("group", { name: "History range" })).toHaveCount(0);
    await expect(main.getByRole("group", { name: "Inventory" })).toBeVisible();
    await main.getByRole("group", { name: "Inventory" }).getByRole("button", { name: /Virtual Machines/i }).click();
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

  test("the Tasks page filters tasks and Exports is in the Protection group", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter?tab=activity");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: /^Tasks/ })).toBeVisible();
    const filters = main.getByRole("group", { name: "Task filters" });
    await filters.getByLabel("Status").selectOption("echec");
    await filters.getByLabel("Period").selectOption("all");
    await expect(main.getByRole("button", { name: "Export as CSV" })).toBeVisible();
    // Exports now sits in the Protection group, always visible.
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("group", { name: "Protection" }).getByRole("button", { name: "Exports" })).toBeVisible();
  });

  test("a VM page shows configuration and protection, and every historical operation in the Actions menu", async ({ page }) => {
    await nextLogin(page);
    await page.goto("/datacenter?tab=vms");
    const vm = page.getByRole("main").getByRole("button", { name: VM }).first();
    await expect(vm).toBeVisible({ timeout: 30_000 });
    await vm.click();
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { name: "Configuration" })).toBeVisible();
    await expect(main.getByRole("heading", { name: "Protection" })).toBeVisible();
    // R7 / R8: no "All operations" block and no chart on the summary.
    await expect(main.getByText("All operations")).toHaveCount(0);
    await expect(main.getByRole("heading", { name: /^Performance · last hour/ })).toHaveCount(0);
    for (const tab of ["Summary", "Performance", "Snapshots", "Backups", "Hardware", "Network", "Console", "Tasks"]) await expect(main.getByRole("tab", { name: tab, exact: true })).toBeVisible();
    // R6: Snapshot and Migrate left the header for the grouped Actions menu, with every historical operation.
    const head = page.locator(".nx-oh-acts");
    for (const action of ["Snapshot", "Migrate…"]) await expect(head.getByRole("button", { name: new RegExp(`^${action}`) })).toHaveCount(0);
    await page.getByRole("button", { name: /^Actions/ }).click();
    const menu = page.getByRole("menu");
    for (const g of ["Power", "Protection", "Lifecycle"]) await expect(menu.getByText(g, { exact: true })).toBeVisible();
    for (const item of [/Force stop/, /^Restart/, /^Create a snapshot/, /^Back up now/, /^HA protection/, /^Clone/, /^Migrate/, /^Convert to template/, /^Export/, /^Automatic clean-up/, /^Copy link/, /^Delete the VM/]) await expect(menu.getByRole("menuitem", { name: item })).toBeVisible();
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
    // R2: one Activity button in the top bar; the sidebar has no Alerts entry any more.
    await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /^Alerts/ })).toHaveCount(0);
    await page.getByRole("banner").getByRole("button", { name: /^Activity/ }).click();
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("tab", { name: /^Alerts/ })).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    // a second click on the same button closes the panel
    const activity = page.getByRole("banner").getByRole("button", { name: /^Activity/ });
    await activity.click();
    await expect(panel).toBeVisible();
    await expect(activity).toHaveAttribute("aria-expanded", "true");
    await activity.click();
    await expect(panel).toBeHidden();
    await expect(activity).toHaveAttribute("aria-expanded", "false");
    await activity.click();
    await panel.getByRole("tab", { name: /^Running tasks/ }).click();
    await expect(panel.getByText("No task running.")).toBeVisible();
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
    await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);
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
