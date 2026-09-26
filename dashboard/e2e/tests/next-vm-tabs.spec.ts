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
  await page.getByLabel(/Password|Mot de passe/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto(`/vm/${NAME}?tab=${tab}`);
}

test("options: values are validated, resources saved on a stopped VM, limits applied live", async ({ page, request }) => {
  await open(page, "options");
  const main = page.getByRole("main");
  const save = main.getByRole("button", { name: "Save", exact: true });
  await expect(main.getByLabel("vCPU count")).toBeEnabled({ timeout: 20_000 });
  await expect(save).toBeDisabled(); // unchanged
  await main.getByLabel("vCPU count").fill("0");
  await expect(save).toBeDisabled();
  await expect(main.getByText(/^From \d+ to/).first()).toHaveClass(/nx-hint--error/);
  await main.getByLabel("vCPU count").fill("2");
  await save.click();
  await expect.poll(async () => ((await (await request.get(`/vms/${NAME}`, { headers: auth() })).json()) as { vcpu: number }).vcpu, { timeout: 30_000 }).toBe(2);

  const apply = main.getByRole("button", { name: "Apply" });
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
  await main.getByLabel("New disk size in GB").fill("0");
  await expect(main.getByRole("button", { name: "Attach", exact: true })).toBeDisabled();
  await main.getByLabel("New disk size in GB").fill("1");
  await main.getByRole("button", { name: "Attach", exact: true }).click();
  await expect.poll(disks, { timeout: 30_000 }).toBe(before + 1);
  await main.getByRole("button", { name: /^Detach disk sd/ }).first().click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  expect(await disks()).toBe(before + 1);
  await main.getByRole("button", { name: /^Detach disk sd/ }).first().click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Detach", exact: true }).click();
  await expect.poll(disks, { timeout: 30_000 }).toBe(before);
  // interfaces and firewall moved to their own Network tab
  await expect(main.getByRole("heading", { level: 2, name: /^Network interfaces/ })).toHaveCount(0);
  await main.getByRole("tab", { name: "Options" }).click();
  await expect(page).toHaveURL(/tab=options/);

  await page.goto(`/vm/${NAME}?tab=network`);
  await expect(main.getByRole("tab", { name: "Network", exact: true })).toHaveAttribute("aria-selected", "true");
  await main.getByLabel("VLAN (optional)").fill("5000");
  await expect(main.getByRole("button", { name: "Add an interface" })).toBeDisabled();
  await expect(main.getByText("VLAN from 1 to 4094.")).toBeVisible();
  await expect(main.getByRole("heading", { level: 2, name: /^Network interfaces/ })).toBeVisible();
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
  await expect(main.getByRole("row", { name: /before-upgrade/ })).toBeVisible({ timeout: 20_000 });

  await main.getByRole("button", { name: "Restore snapshot before-upgrade" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await main.getByRole("button", { name: "Restore snapshot before-upgrade" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Restore", exact: true }).click();
  await expect(page.getByText("Snapshot restored").first()).toBeVisible({ timeout: 60_000 });

  await main.getByRole("button", { name: "Delete snapshot before-upgrade" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
  await expect.poll(snaps, { timeout: 30_000 }).not.toContain("before-upgrade");
});

test("header actions and performance: Snapshot opens the creation dialog, Migrate explains why it is unavailable", async ({ page }) => {
  await open(page, "summary");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 2, name: "Configuration" })).toBeVisible({ timeout: 20_000 });
  await expect(main.getByRole("heading", { level: 2, name: /^Performance · last hour/ })).toBeVisible();
  for (const tab of ["Summary", "Performance", "Snapshots", "Backups", "Hardware", "Network", "Console"]) await expect(main.getByRole("tab", { name: tab, exact: true })).toBeVisible();
  const head = page.locator(".nx-headactions");
  // stopped VM on a single-node install: Start is the primary action, Migrate says why it is unavailable
  await expect(head.getByRole("button", { name: "Start", exact: true })).toBeVisible();
  await expect(head.getByRole("button", { name: /^Migrate…/ })).toHaveAttribute("aria-disabled", "true");
  await expect(head.getByRole("button", { name: /^Migrate…/ })).toHaveAttribute("title", /Not running|No other online node/);
  await head.getByRole("button", { name: "Snapshot", exact: true }).click();
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
  await expect(main.getByRole("heading", { name: "Scheduled backup" })).toBeVisible({ timeout: 20_000 });
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

  await main.getByRole("button", { name: "Disable", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Disable", exact: true }).click();
  await expect.poll(async () => (await (await request.get(`/vms/${NAME}/backup-schedule`, { headers: auth() })).text()), { timeout: 20_000 }).toMatch(/null|^$/);
});

test("French labels and no overflow on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await open(page, "options", "fr");
  await expect(page.getByRole("main").getByRole("heading", { name: "Limites et priorité (cgroups)" })).toBeVisible({ timeout: 20_000 });
  await page.goto(`/vm/${NAME}?tab=backup`);
  await expect(page.getByRole("main").getByRole("heading", { name: "Sauvegarde planifiée" })).toBeVisible({ timeout: 20_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
