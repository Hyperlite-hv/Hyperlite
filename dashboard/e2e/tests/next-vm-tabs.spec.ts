import type { Page } from "@playwright/test";
import { ADMIN, apiLogin, expect, PREFIX, test } from "../support/fixtures";

// VM Configure / Snapshots / Backup pages against a real (stopped) VM on the real backend.
const stamp = Date.now().toString().slice(-6);
const NAME = `${PREFIX}tabs-${stamp}`;
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });
test.describe.configure({ mode: "serial", timeout: 180_000 });

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
  const res = await request.post("/vms", { headers: auth(), data: { name: NAME, vcpu: 1, memory_mb: 256, disks: [{ size_gb: 3 }], network: "hyperlite-isolated", username: "tester", password: "Testpass1" } });
  expect(res.ok(), await res.text()).toBe(true);
  await expect.poll(async () => (await request.get(`/vms/${NAME}`, { headers: auth() })).ok(), { timeout: 90_000 }).toBe(true);
});
test.afterAll(async ({ request }) => {
  const backups = (await (await request.get(`/vms/${NAME}/backups`, { headers: auth() })).json().catch(() => [])) as { id: number }[];
  for (const b of backups) await request.delete(`/backups/${b.id}`, { headers: auth() }).catch(() => {});
  await request.delete(`/vms/${NAME}/backup-schedule`, { headers: auth() }).catch(() => {});
  await request.post(`/vms/${NAME}/stop?force=true`, { headers: auth() }).catch(() => {});
  await request.delete(`/vms/${NAME}?confirm=true`, { headers: auth() }).catch(() => {});
});

async function open(page: Page, tab: string, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto(`/vm/${NAME}?tab=${tab}`);
}

test("hardware and options: values are validated, resources saved on a stopped VM, limits applied live", async ({ page, request }) => {
  // R9: vCPU and memory are edited in Hardware › Processor and memory.
  await open(page, "hardware");
  const main = page.getByRole("main");
  const compute = main.getByRole("region", { name: "Processor and memory" });
  const save = compute.getByRole("button", { name: "Save", exact: true });
  await expect(compute.getByLabel("vCPU count")).toBeEnabled({ timeout: 20_000 });
  await expect(save).toBeDisabled(); // unchanged
  await compute.getByLabel("vCPU count").fill("0");
  await expect(save).toBeDisabled();
  await expect(compute.getByText(/^From \d+ to/).first()).toHaveClass(/is-error/);
  await compute.getByLabel("vCPU count").fill("2");
  await save.click();
  await expect.poll(async () => ((await (await request.get(`/vms/${NAME}`, { headers: auth() })).json()) as { vcpu: number }).vcpu, { timeout: 30_000 }).toBe(2);

  // "Options and limits" keeps only the live cgroups limits, with one "Apply live" button.
  await main.getByRole("navigation", { name: "Hardware" }).getByRole("button", { name: "Options and limits" }).click();
  await expect(page).toHaveURL(/tab=options/);
  await expect(main.getByLabel("vCPU count")).toHaveCount(0);
  const apply = main.getByRole("button", { name: "Apply live" });
  await main.getByLabel("CPU shares").fill("1");
  await expect(apply).toBeDisabled();
  await expect(main.getByText("Whole number from 2 to 262144.")).toBeVisible();
  await main.getByLabel("CPU shares").fill("2048");
  // under load the card can still be settling after the resources save: apply only once the value is really in the form
  await expect(main.getByLabel("CPU shares")).toHaveValue("2048");
  await expect(apply).toBeEnabled();
  await apply.click();
  await expect(page.getByText("Limits applied").first()).toBeVisible({ timeout: 20_000 });
  await expect.poll(async () => ((await (await request.get(`/vms/${NAME}/limits`, { headers: auth() })).json()) as { cpu_shares: number }).cpu_shares, { timeout: 20_000 }).toBe(2048);
});

test("hardware and network: attach and detach a disk with confirmation, VLAN is validated, interface list is shown", async ({ page, request }) => {
  await open(page, "hardware");
  const main = page.getByRole("main");
  const disks = async () => ((await (await request.get(`/vms/${NAME}/disks`, { headers: auth() })).json()) as unknown[]).length;
  const before = await disks();
  await expect(main.getByRole("heading", { level: 2, name: /^Disks/ })).toBeVisible({ timeout: 20_000 });
  // Adding a disk opens a side drawer.
  await main.getByRole("button", { name: "Add a disk" }).click();
  const drawer = page.getByRole("dialog", { name: "Add a disk" });
  await drawer.getByLabel("New disk size in GB").fill("0");
  await expect(drawer.getByRole("button", { name: "Add the disk", exact: true })).toBeDisabled();
  await drawer.getByLabel("New disk size in GB").fill("1");
  await drawer.getByRole("button", { name: "Add the disk", exact: true }).click();
  await expect.poll(disks, { timeout: 30_000 }).toBe(before + 1);
  await main.getByRole("button", { name: /^Detach disk sd/ }).first().click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  expect(await disks()).toBe(before + 1);
  await main.getByRole("button", { name: /^Detach disk sd/ }).first().click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Detach", exact: true }).click();
  await expect.poll(disks, { timeout: 30_000 }).toBe(before);
  // interfaces and firewall moved to their own Network tab
  await expect(main.getByRole("heading", { level: 2, name: /^Network interfaces/ })).toHaveCount(0);
  await main.getByRole("navigation", { name: "Hardware" }).getByRole("button", { name: "Options and limits" }).click();
  await expect(page).toHaveURL(/tab=options/);

  await page.goto(`/vm/${NAME}?tab=network`);
  await expect(main.getByRole("tab", { name: "Network", exact: true })).toHaveAttribute("aria-selected", "true");
  await main.getByRole("button", { name: "Add an interface" }).click();
  const ifDrawer = page.getByRole("dialog", { name: "Add an interface" });
  await ifDrawer.getByLabel("VLAN (optional)").fill("5000");
  await expect(ifDrawer.getByRole("button", { name: "Add the interface" })).toBeDisabled();
  await expect(ifDrawer.getByText("VLAN from 1 to 4094.")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(main.getByRole("heading", { level: 2, name: /^Network interfaces/ })).toBeVisible();
});

test("hardware: a disk is grown from a drawer, a shrink is refused, the French labels are shown", async ({ page, request }) => {
  type Disk = { cible: string; type: string; taille_go: number | null; non_agrandissable: string | null };
  const firstDisk = async () => ((await (await request.get(`/vms/${NAME}/disks`, { headers: auth() })).json()) as Disk[]).find((d) => d.type === "disk")!;
  const disk = await firstDisk();
  expect(disk.non_agrandissable).toBeNull();
  const target = Math.floor(disk.taille_go ?? 3) + 1;

  // The API refuses a shrink and changes nothing.
  const shrink = await request.post(`/vms/${NAME}/disks/${disk.cible}/resize`, { headers: auth(), data: { size_gb: 1 } });
  expect(shrink.status()).toBe(422);
  expect(await shrink.text()).toContain("Shrinking");

  await open(page, "hardware");
  const main = page.getByRole("main");
  await main.getByRole("button", { name: `Grow disk ${disk.cible}` }).click();
  const drawer = page.getByRole("dialog", { name: `Grow disk ${disk.cible}` });
  const grow = drawer.getByRole("button", { name: "Grow the disk", exact: true });
  // The current size is not a growth: the button stays disabled until the size is larger.
  await drawer.getByLabel("New size in GB").fill(String(target - 1));
  await expect(grow).toBeDisabled();
  await expect(drawer.getByText(/^Whole number from \d+ to (\d+|∞) \(larger than the current size\)/)).toBeVisible();
  await drawer.getByLabel("New size in GB").fill(String(target));
  await grow.click();
  await expect(page.getByText("Disk grown").first()).toBeVisible({ timeout: 30_000 });
  await expect.poll(async () => (await firstDisk()).taille_go, { timeout: 30_000 }).toBe(target);

  // Same page in French (the language is stored next to the session, a reload applies it).
  await page.evaluate(() => localStorage.setItem("hyperlite-next-lang", "fr"));
  await page.reload();
  await main.getByRole("button", { name: `Agrandir le disque ${disk.cible}` }).click();
  await expect(page.getByRole("dialog", { name: `Agrandir le disque ${disk.cible}` }).getByRole("button", { name: "Agrandir le disque", exact: true })).toBeVisible();
});

test("snapshots: name is validated, create, restore and delete are confirmed and really happen", async ({ page, request }) => {
  await open(page, "snapshots");
  const main = page.getByRole("main");
  const snaps = async () => ((await (await request.get(`/vms/${NAME}/snapshots`, { headers: auth() })).json()) as { nom: string }[]).map((s) => s.nom);
  await main.getByRole("button", { name: "Create a snapshot" }).click();
  const dlg = page.getByRole("dialog");
  await dlg.getByRole("textbox").fill("bad name!");
  await dlg.getByRole("button", { name: "Create a snapshot" }).click();
  await expect(dlg).toContainText("Letters, digits");
  await dlg.getByRole("textbox").fill("before-upgrade");
  await dlg.getByRole("button", { name: "Create a snapshot" }).click();
  await expect.poll(snaps, { timeout: 60_000 }).toContain("before-upgrade");
  // The snapshots are a timeline now.
  await expect(main.getByRole("list", { name: "Snapshot timeline" }).getByText("before-upgrade", { exact: true })).toBeVisible({ timeout: 20_000 });

  await main.getByRole("button", { name: "Restore snapshot before-upgrade" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await main.getByRole("button", { name: "Restore snapshot before-upgrade" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Restore", exact: true }).click();
  await expect(page.getByText("Snapshot restored").first()).toBeVisible({ timeout: 60_000 });

  await main.getByRole("button", { name: "Delete snapshot before-upgrade" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
  await expect.poll(snaps, { timeout: 30_000 }).not.toContain("before-upgrade");
});

test("header actions and performance: Snapshot and Migrate are in the Actions menu, charts only in Performance", async ({ page }) => {
  await open(page, "summary");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 2, name: "Configuration" })).toBeVisible({ timeout: 20_000 });
  // R8: KPI tiles on the summary, the full charts only in the Performance tab.
  await expect(main.getByRole("heading", { level: 2, name: /^Performance · last hour/ })).toHaveCount(0);
  for (const tab of ["Summary", "Performance", "Snapshots", "Backups", "Hardware", "Network", "Console"]) await expect(main.getByRole("tab", { name: tab, exact: true })).toBeVisible();
  const head = page.locator(".nx-oh-acts");
  // stopped VM on a single-node install: Start is the primary action, Migrate says why it is unavailable
  await expect(head.getByRole("button", { name: "Start", exact: true })).toBeVisible();
  await head.getByRole("button", { name: /^Actions/ }).click();
  const migrate = page.getByRole("menuitem", { name: /^Migrate…/ });
  await expect(migrate).toHaveAttribute("aria-disabled", "true");
  await expect(migrate).toHaveAttribute("title", /Not running|No other online node/);
  await page.getByRole("menuitem", { name: "Create a snapshot" }).click();
  await expect(page).toHaveURL(/tab=snapshots/);
  const dlg = page.getByRole("dialog");
  await expect(dlg.getByRole("textbox")).toBeVisible({ timeout: 20_000 });
  await dlg.getByRole("button", { name: "Cancel" }).click();

  await main.getByRole("tab", { name: "Performance" }).click();
  await expect(main.getByRole("group", { name: "History range" })).toBeVisible();
  for (const h of ["CPU", "Memory", "Disk I/O", "Network"]) await expect(main.getByRole("heading", { level: 3, name: h, exact: true })).toBeVisible();
  await main.getByRole("group", { name: "History range" }).getByRole("button", { name: "7j" }).click();
  await expect(main.getByRole("group", { name: "History range" }).getByRole("button", { name: "7j" })).toHaveAttribute("aria-pressed", "true");
});

test("backup: schedule is validated and saved, a backup runs and can be deleted after a confirmation", async ({ page, request }) => {
  await open(page, "backup");
  const main = page.getByRole("main");
  const save = main.getByRole("button", { name: "Save", exact: true });
  // R10: "Back up now" is the tab primary, the schedule card has a switch and a default Save.
  await expect(main.getByRole("heading", { name: "Schedule" })).toBeVisible({ timeout: 20_000 });
  await main.getByRole("switch", { name: "Scheduled backup" }).click();
  await main.getByLabel("Retention (backups kept)").fill("0");
  await expect(save).toBeDisabled();
  await expect(main.getByText("Enter a whole number from 1 to 365.")).toBeVisible();
  await main.getByLabel("Retention (backups kept)").fill("3");
  await main.getByLabel("Backup time").fill("03:30");
  await save.click();
  await expect.poll(async () => { const r = await request.get(`/vms/${NAME}/backup-schedule`, { headers: auth() }); return r.ok() ? ((await r.json()) as { retention_count: number; heure: string } | null) : null; }, { timeout: 20_000 }).toMatchObject({ retention_count: 3, heure: "03:30" });

  await main.getByRole("button", { name: "Back up now" }).click();
  await expect(main.getByRole("row", { name: /Cold/ })).toBeVisible({ timeout: 90_000 });
  await expect(main.getByRole("row", { name: /Cold/ }).getByText("Done")).toBeVisible({ timeout: 120_000 });
  await main.getByRole("button", { name: /^Delete backup #/ }).first().click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await main.getByRole("button", { name: /^Delete backup #/ }).first().click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(main.getByText("No backups")).toBeVisible({ timeout: 30_000 });

  await main.getByRole("switch", { name: "Scheduled backup" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Disable", exact: true }).click();
  await expect.poll(async () => (await (await request.get(`/vms/${NAME}/backup-schedule`, { headers: auth() })).text()), { timeout: 20_000 }).toMatch(/null|^$/);
});

test("console: the workstation panel gives the hyperlite links, the commands and the client", async ({ page }) => {
  await open(page, "console");
  await page.getByRole("button", { name: "From your workstation" }).click();
  const panel = page.getByRole("dialog", { name: "Access from your workstation" });
  await expect(panel.getByRole("link", { name: "Open in a terminal (SSH)" })).toHaveAttribute("href", new RegExp(`^hyperlite://ssh/${NAME}\\?server=`));
  // first time on a computer: one download (a double-click installs it), offered when the server has the builds
  await expect(panel.getByRole("link", { name: /^Download hyperlite/ }).or(panel.getByText("The client is not available on this server"))).toBeVisible();
  // the commands stay available, folded under the advanced options
  await expect(panel.getByLabel("SSH command", { exact: true })).toBeHidden();
  await panel.getByText("Advanced options").click();
  await expect(panel.getByLabel("SSH command", { exact: true })).toHaveText(`hyperlite ssh ${NAME}`);
  await expect(panel.getByLabel("Sign-in command", { exact: true })).toContainText("hyperlite login http");
  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
});

test("French labels and no overflow on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await open(page, "options", "fr");
  await expect(page.getByRole("main").getByRole("heading", { name: "Limites et priorité (cgroups)" })).toBeVisible({ timeout: 20_000 });
  await page.goto(`/vm/${NAME}?tab=backup`);
  await expect(page.getByRole("main").getByRole("heading", { name: "Planification" })).toBeVisible({ timeout: 20_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
