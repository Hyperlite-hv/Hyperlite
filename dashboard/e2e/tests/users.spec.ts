import { ADMIN, apiLogin, expect, PREFIX, test, uiLogin } from "../support/fixtures";

const stamp = Date.now().toString().slice(-6);
const OBSERVER = { username: `${PREFIX}obs-${stamp}`, password: "Obs-Passw0rd!" };

test.describe.configure({ mode: "serial" });

test.afterAll(async ({ request }) => {
  const token = await apiLogin(request);
  await request.delete(`/auth/users/${OBSERVER.username}`, { headers: { Authorization: `Bearer ${token}` } });
});

test.describe("Users and permissions", () => {
  test("an administrator creates a read-only user from the Permissions tab", async ({ page, request, problems }) => {
    await uiLogin(page);
    await page.getByRole("tab", { name: "Permissions", exact: true }).click();
    await page.getByRole("textbox", { name: "Username" }).fill(OBSERVER.username);
    await page.getByRole("textbox", { name: /Password/ }).fill(OBSERVER.password);
    await page.getByRole("combobox", { name: "Role of the new user" }).selectOption("observateur");
    await page.getByRole("button", { name: "Create", exact: true }).first().click();
    await expect(page.getByRole("combobox", { name: `Role of ${OBSERVER.username}` })).toBeVisible();

    // The user really exists on the backend, with the read-only role.
    const token = await apiLogin(request);
    const users = await (await request.get("/auth/users", { headers: { Authorization: `Bearer ${token}` } })).json();
    const created = users.find((u: { username: string }) => u.username === OBSERVER.username);
    expect(created?.role).toBe("observateur");

    // The list is still correct after a reload.
    await page.reload();
    await page.getByRole("tab", { name: "Permissions", exact: true }).click();
    await expect(page.getByRole("combobox", { name: `Role of ${OBSERVER.username}` })).toBeVisible();
    expect(problems.filter((p) => !/Failed to load resource/.test(p))).toEqual([]);
  });

  test("refuses a duplicate user name with a clear message", async ({ page }) => {
    await uiLogin(page);
    await page.getByRole("tab", { name: "Permissions", exact: true }).click();
    await page.getByRole("textbox", { name: "Username" }).fill(OBSERVER.username);
    await page.getByRole("textbox", { name: /Password/ }).fill(OBSERVER.password);
    await page.getByRole("button", { name: "Create", exact: true }).first().click();
    await expect(page.getByText(/already exists|Creation failed/i).first()).toBeVisible();
  });

  test("the new user signs in and does not see administrative actions", async ({ page }) => {
    await uiLogin(page, OBSERVER.username, OBSERVER.password);
    await expect(page.getByRole("button", { name: "Create VM" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Create container" })).toHaveCount(0);
  });

  test("the backend refuses administrative calls from the read-only user (not just the UI)", async ({ request }) => {
    const token = await apiLogin(request, OBSERVER.username, OBSERVER.password);
    const h = { Authorization: `Bearer ${token}` };
    const denied: Array<[string, () => Promise<{ status(): number }>]> = [
      ["create VM", () => request.post("/vms", { headers: h, data: { name: `${PREFIX}denied`, vcpu: 1, memory_mb: 256, disks: [{ size_gb: 1 }], network: "default", username: "u", password: "pppp" } })],
      ["create user", () => request.post("/auth/users", { headers: h, data: { username: `${PREFIX}x`, password: "pppp", role: "admin" } })],
      ["delete user", () => request.delete(`/auth/users/${ADMIN.username}`, { headers: h })],
      ["create network", () => request.post("/networks", { headers: h, data: { name: `${PREFIX}n`, mode: "isole" } })],
      ["create storage pool", () => request.post("/storage", { headers: h, data: { name: `${PREFIX}p`, type: "dir" } })],
      ["update profile", () => request.put("/host/profile", { headers: h, data: { profil: "standard" } })],
      ["SSO config", () => request.get("/auth/sso/config", { headers: h })],
      ["host shell ticket", () => request.post("/host/terminal-ticket", { headers: h })],
    ];
    for (const [name, call] of denied) {
      const res = await call();
      expect(res.status(), `${name} must be forbidden for an observer`).toBe(403);
    }
    // Read access stays available.
    expect((await request.get("/vms", { headers: h })).ok()).toBeTruthy();
  });

  test("an observer cannot read notification channel secrets", async ({ request }) => {
    const adminToken = await apiLogin(request);
    const ah = { Authorization: `Bearer ${adminToken}` };
    const created = await request.post("/notifications/channels", { headers: ah, data: { name: `${PREFIX}hook`, type: "webhook", config: { url: "https://example.invalid/hook?token=s3cret" }, events: [] } });
    expect(created.ok()).toBeTruthy();
    const { id } = await created.json();
    try {
      const obsToken = await apiLogin(request, OBSERVER.username, OBSERVER.password);
      const r = await request.get("/notifications/channels", { headers: { Authorization: `Bearer ${obsToken}` } });
      expect(JSON.stringify(await r.json())).not.toContain("s3cret");
    } finally {
      await request.delete(`/notifications/channels/${id}`, { headers: ah });
    }
  });

  test("an administrator deletes the user after confirming", async ({ page, request }) => {
    await uiLogin(page);
    await page.getByRole("tab", { name: "Permissions", exact: true }).click();
    page.once("dialog", (d) => {
      expect(d.message()).toContain(OBSERVER.username);
      void d.accept();
    });
    await page.getByRole("combobox", { name: `Role of ${OBSERVER.username}` }).locator("xpath=..").getByRole("button").click();
    await expect(page.getByRole("combobox", { name: `Role of ${OBSERVER.username}` })).toHaveCount(0);
    const token = await apiLogin(request);
    const users = await (await request.get("/auth/users", { headers: { Authorization: `Bearer ${token}` } })).json();
    expect(users.map((u: { username: string }) => u.username)).not.toContain(OBSERVER.username);
    const login = await request.post("/auth/login", { form: { username: OBSERVER.username, password: OBSERVER.password } });
    expect(login.status()).toBe(401);
  });
});
