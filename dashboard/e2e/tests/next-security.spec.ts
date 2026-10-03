import type { Page } from "@playwright/test";
import { ADMIN, apiLogin, expect, PREFIX, test } from "../support/fixtures";

// Security page against the real backend: users, groups, pools, custom roles and assignments.
const stamp = Date.now().toString().slice(-6);
const USER = `${PREFIX}sec-${stamp}`;
const GROUP = `${PREFIX}grp-${stamp}`;
const POOL = `${PREFIX}vmpool-${stamp}`;
const ROLE = `${PREFIX}role-${stamp}`;
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });
test.describe.configure({ mode: "serial", timeout: 120_000 });
test.beforeAll(async ({ request }) => { token = await apiLogin(request); });
test.afterAll(async ({ request }) => {
  const h = auth();
  // A missing route answers a JSON 404 (it used to answer the dashboard page): only a list is walked.
  const get = async (p: string) => { const r = await request.get(p, { headers: h }); const body = r.ok() ? await r.json() : []; return (Array.isArray(body) ? body : []) as { id: number; name?: string; key?: string; label?: string }[]; };
  // Only this file's assignments: other files run at the same time and keep theirs.
  for (const a of (await get("/acl").catch(() => [])) as { id: number; subject_label?: string }[]) if (a.subject_label === USER || a.subject_label === GROUP) await request.delete(`/acl/${a.id}`, { headers: h }).catch(() => {});
  for (const g of await get("/groups").catch(() => [])) if (g.name === GROUP) await request.delete(`/groups/${g.id}`, { headers: h }).catch(() => {});
  for (const p of await get("/pools").catch(() => [])) if (p.name === POOL) await request.delete(`/pools/${p.id}`, { headers: h }).catch(() => {});
  for (const r of await get("/acl/custom-roles").catch(() => [])) if ((r.label ?? r.name) === ROLE) await request.delete(`/acl/custom-roles/${r.id}`, { headers: h }).catch(() => {});
  await request.delete(`/auth/users/${USER}`, { headers: h }).catch(() => {});
});

async function open(page: Page) {
  await page.addInitScript(() => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "en"); } });
  await page.goto("/");
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=permissions");
  await expect(page.getByRole("main").getByRole("tab", { name: /^Users/ })).toHaveAttribute("aria-selected", "true", { timeout: 20_000 });
}
// R12: one sub-tab per object, each with one explicit primary that opens its form in a side drawer.
const tab = (page: Page, name: RegExp) => page.getByRole("main").getByRole("tab", { name }).click();
async function drawer(page: Page, primary: string) {
  await page.getByRole("main").getByRole("button", { name: primary, exact: true }).click();
  return page.getByRole("dialog", { name: primary });
}
const dialogConfirm = async (page: Page, name: string | RegExp) => page.getByRole("alertdialog").getByRole("button", { name, exact: typeof name === "string" }).click();

test("users: create with validation, promotion needs a confirmation, self is protected, deletion is confirmed", async ({ page, request }) => {
  await open(page);
  const main = page.getByRole("main");
  const form = await drawer(page, "Create a user");
  const create = form.getByRole("button", { name: "Create the user", exact: true });
  await form.getByLabel("Username", { exact: true }).fill(USER);
  await form.getByLabel("Password", { exact: true }).fill("abc");
  await expect(create).toBeDisabled(); // password shorter than the minimum
  await form.getByLabel("Password", { exact: true }).fill("Correct-Horse-1");
  await create.click();
  const roleSel = main.getByRole("combobox", { name: `Role of ${USER}` });
  await expect(roleSel).toHaveValue("observateur");

  // promoting to administrator: cancel keeps the role, confirm applies it
  await roleSel.selectOption("admin");
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await expect(roleSel).toHaveValue("observateur");
  await roleSel.selectOption("admin");
  await dialogConfirm(page, "Make administrator");
  await expect(roleSel).toHaveValue("admin");
  const users = (await (await request.get("/auth/users", { headers: auth() })).json()) as { username: string; role: string }[];
  expect(users.find((u) => u.username === USER)?.role).toBe("admin");
  // demoting an administrator asks too: cancel keeps the role, confirm applies it
  await roleSel.selectOption("observateur");
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await expect(roleSel).toHaveValue("admin");
  await roleSel.selectOption("observateur");
  await dialogConfirm(page, "Remove the rights");
  await expect(roleSel).toHaveValue("observateur");

  // an administrator can neither demote nor delete themself here
  await expect(main.getByRole("combobox", { name: `Role of ${ADMIN.username}` })).toBeDisabled();
  await expect(main.getByRole("button", { name: "You cannot delete yourself" })).toBeDisabled();
});

test("groups, pools, custom roles and assignments: full lifecycle with confirmations", async ({ page, request }) => {
  await open(page);
  const main = page.getByRole("main");

  await tab(page, /^Groups/);
  const groupForm = await drawer(page, "Create a group");
  await groupForm.getByLabel("Group name (e.g. devs)").fill(GROUP);
  await groupForm.getByRole("button", { name: "Create the group", exact: true }).click();
  await expect(main.getByRole("button", { name: `Delete group ${GROUP}` })).toBeVisible();
  await main.getByLabel(`Add a member to ${GROUP}`).selectOption(USER);
  // the group's own section: other files may create groups at the same time
  await main.getByRole("region", { name: GROUP }).getByRole("button", { name: "Add", exact: true }).click();
  await expect(main.getByRole("button", { name: `Remove ${USER}` })).toBeVisible();
  await main.getByRole("button", { name: `Remove ${USER}` }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await expect(main.getByRole("button", { name: `Remove ${USER}` })).toBeVisible();

  await tab(page, /^VM pools/);
  const poolForm = await drawer(page, "Create a VM pool");
  await poolForm.getByLabel("Pool name (e.g. project-a)").fill(POOL);
  await poolForm.getByRole("button", { name: "Create the pool", exact: true }).click();
  await expect(main.getByRole("button", { name: `Delete pool ${POOL}` })).toBeVisible();

  await tab(page, /^Roles/);
  const roleForm = await drawer(page, "Create a role");
  await roleForm.getByLabel("Role name").fill(ROLE);
  await roleForm.getByRole("checkbox").first().check();
  await roleForm.getByRole("button", { name: /^Create \(1 selected\)/ }).click();
  await expect(main.getByRole("button", { name: `Delete role ${ROLE}` })).toBeVisible();

  // assignment of the custom role to the user on the pool
  await tab(page, /^Assignments/);
  const aclForm = await drawer(page, "Assign a role");
  await aclForm.getByLabel("Subject").selectOption(USER);
  await aclForm.getByLabel("Role", { exact: true }).selectOption({ label: ROLE });
  await aclForm.getByLabel("On", { exact: true }).selectOption("pool");
  await aclForm.getByLabel("Resource").selectOption({ label: POOL });
  await aclForm.getByRole("button", { name: "Assign", exact: true }).click();
  await expect.poll(async () => {
    const acl = (await (await request.get("/acl", { headers: auth() })).json()) as { subject_label: string; resource_type: string }[];
    return acl.some((a) => a.subject_label === USER && a.resource_type === "pool");
  }).toBe(true);
  await expect(main.getByRole("row", { name: new RegExp(`${USER}.*${ROLE}`) })).toContainText(POOL);

  // remove everything through the UI, each step behind a confirmation
  await main.getByRole("button", { name: /^Remove assignment/ }).first().click();
  await dialogConfirm(page, "Remove");
  await expect(main.getByText("No assignments")).toBeVisible({ timeout: 15_000 });
  await tab(page, /^Roles/);
  await main.getByRole("button", { name: `Delete role ${ROLE}` }).click();
  await dialogConfirm(page, "Delete");
  await tab(page, /^VM pools/);
  await main.getByRole("button", { name: `Delete pool ${POOL}` }).click();
  await dialogConfirm(page, "Delete");
  await tab(page, /^Groups/);
  await main.getByRole("button", { name: `Delete group ${GROUP}` }).click();
  await dialogConfirm(page, "Delete");
  await expect(main.getByRole("button", { name: `Delete group ${GROUP}` })).toHaveCount(0, { timeout: 15_000 });
  await tab(page, /^Users/);
  await main.getByRole("button", { name: `Delete user ${USER}` }).click();
  await dialogConfirm(page, "Delete");
  await expect(main.getByRole("combobox", { name: `Role of ${USER}` })).toHaveCount(0, { timeout: 15_000 });
});

test("French labels and no overflow on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await page.addInitScript(() => { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", "fr"); });
  await page.goto("/");
  await page.getByLabel(/Nom d.utilisateur|Username/).fill(ADMIN.username);
  await page.getByLabel(/Mot de passe|Password/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Se connecter|Sign in/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  await page.goto("/datacenter?tab=permissions");
  for (const name of [/^Utilisateurs/, /^Groupes/, /^Rôles/, /^Pools de VM/, /^Attributions/]) await expect(page.getByRole("main").getByRole("tab", { name })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("main").getByRole("button", { name: "Créer un utilisateur" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
