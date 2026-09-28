import { apiLogin, expect, PREFIX, test, uiLogin } from "../support/fixtures";

// Password change and reset against the real backend: an administrator resets another account (its sessions
// and tokens stop working), and a user changes their own password (wrong current one refused, other sessions
// signed out, this one kept).
const stamp = Date.now().toString().slice(-6);
const RESET_USER = `${PREFIX}pwreset-${stamp}`;
const SELF_USER = `${PREFIX}pwself-${stamp}`;
const INITIAL = "Initial-Tangerine-9";
const NEW_SELF = "Second-Orbit-Lantern-4";
let admin = "";
const auth = () => ({ Authorization: `Bearer ${admin}` });

test.describe.configure({ mode: "serial", timeout: 120_000 });
test.beforeAll(async ({ request }) => {
  admin = await apiLogin(request);
  for (const username of [RESET_USER, SELF_USER]) {
    const made = await request.post("/auth/users", { headers: auth(), data: { username, password: INITIAL, role: "observateur" } });
    expect(made.status(), `create ${username}`).toBe(201);
  }
});
test.afterAll(async ({ request }) => {
  for (const username of [RESET_USER, SELF_USER]) await request.delete(`/auth/users/${username}`, { headers: auth() }).catch(() => {});
});

test("an administrator resets a password: weak ones are refused, the old sessions and tokens stop working", async ({ page, request }) => {
  const oldSession = await apiLogin(request, RESET_USER, INITIAL);
  await new Promise((r) => setTimeout(r, 1100)); // session tokens are dated to the second
  await uiLogin(page, undefined, undefined, "/datacenter?tab=permissions");
  const main = page.getByRole("main");
  await main.getByRole("button", { name: `Reset the password of ${RESET_USER}` }).click();
  const dialog = page.getByRole("dialog", { name: `Reset the password of ${RESET_USER}` });
  const submit = dialog.getByRole("button", { name: "Reset the password", exact: true });
  await dialog.getByLabel("New password", { exact: true }).fill("password2024");
  await expect(submit).toBeDisabled();
  await dialog.getByRole("button", { name: "Generate a password" }).click();
  const generated = await dialog.getByLabel("New password", { exact: true }).inputValue();
  expect(generated).toMatch(/^[A-Za-z2-9]{5}(-[A-Za-z2-9]{5}){3}$/);
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(dialog).toHaveCount(0);

  expect((await request.get("/auth/me", { headers: { Authorization: `Bearer ${oldSession}` } })).status()).toBe(401);
  expect((await request.post("/auth/login", { form: { username: RESET_USER, password: INITIAL } })).status()).toBe(401);
  expect((await request.post("/auth/login", { form: { username: RESET_USER, password: generated } })).status()).toBe(200);

  // an administrator cannot reset their own password from this list
  await expect(main.getByRole("button", { name: /use “Change my password”/ })).toBeDisabled();
});

test("a user changes their own password: the current one is checked, other sessions are signed out", async ({ page, request }) => {
  const otherSession = await apiLogin(request, SELF_USER, INITIAL);
  await new Promise((r) => setTimeout(r, 1100));
  await uiLogin(page, SELF_USER, INITIAL, "/datacenter");
  await page.getByRole("button", { name: new RegExp(`^Account — ${SELF_USER}`) }).click();
  await page.getByRole("menuitem", { name: "Change my password" }).click();
  const dialog = page.getByRole("dialog", { name: "Change my password" });
  const submit = page.getByRole("button", { name: "Change the password", exact: true });

  await dialog.getByLabel("Current password", { exact: true }).fill("not-the-password-at-all");
  await dialog.getByLabel("New password", { exact: true }).fill(NEW_SELF);
  await dialog.getByLabel("Confirm the new password", { exact: true }).fill(NEW_SELF);
  await submit.click();
  await expect(dialog.getByText("The current password is incorrect.")).toBeVisible();

  await dialog.getByLabel("Current password", { exact: true }).fill(INITIAL);
  await submit.click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Password changed")).toBeVisible();

  // this session goes on (also after a reload), the other one is signed out
  await page.reload();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
  expect((await request.get("/auth/me", { headers: { Authorization: `Bearer ${otherSession}` } })).status()).toBe(401);
  expect((await request.post("/auth/login", { form: { username: SELF_USER, password: NEW_SELF } })).status()).toBe(200);
});
