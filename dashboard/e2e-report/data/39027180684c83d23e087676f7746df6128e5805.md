# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: users.spec.ts >> Users and permissions >> an administrator deletes the user after confirming
- Location: e2e/tests/users.spec.ts:87:3

# Error details

```
Error: expect(locator).toHaveCount(expected) failed

Locator:  getByRole('combobox', { name: 'Role of e2e-obs-785618' })
Expected: 0
Received: 1
Timeout:  10000ms

Call log:
  - Expect "toHaveCount" getByRole('combobox', { name: 'Role of e2e-obs-785618' }) with timeout 10000ms
  - waiting for getByRole('combobox', { name: 'Role of e2e-obs-785618' })
    24 × locator resolved to 1 element
       - unexpected value "1"

```

# Test source

```ts
  1   | import { ADMIN, apiLogin, expect, PREFIX, test, uiLogin } from "../support/fixtures";
  2   | 
  3   | const stamp = Date.now().toString().slice(-6);
  4   | const OBSERVER = { username: `${PREFIX}obs-${stamp}`, password: "Obs-Passw0rd!" };
  5   | 
  6   | test.describe.configure({ mode: "serial" });
  7   | 
  8   | test.afterAll(async ({ request }) => {
  9   |   const token = await apiLogin(request);
  10  |   await request.delete(`/auth/users/${OBSERVER.username}`, { headers: { Authorization: `Bearer ${token}` } });
  11  | });
  12  | 
  13  | test.describe("Users and permissions", () => {
  14  |   test("an administrator creates a read-only user from the Permissions tab", async ({ page, request, problems }) => {
  15  |     await uiLogin(page);
  16  |     await page.getByRole("tab", { name: "Permissions", exact: true }).click();
  17  |     await page.getByRole("textbox", { name: "Username" }).fill(OBSERVER.username);
  18  |     await page.getByRole("textbox", { name: /Password/ }).fill(OBSERVER.password);
  19  |     await page.getByRole("combobox", { name: "Role of the new user" }).selectOption("observateur");
  20  |     await page.getByRole("button", { name: "Create", exact: true }).first().click();
  21  |     await expect(page.getByRole("combobox", { name: `Role of ${OBSERVER.username}` })).toBeVisible();
  22  | 
  23  |     // The user really exists on the backend, with the read-only role.
  24  |     const token = await apiLogin(request);
  25  |     const users = await (await request.get("/auth/users", { headers: { Authorization: `Bearer ${token}` } })).json();
  26  |     const created = users.find((u: { username: string }) => u.username === OBSERVER.username);
  27  |     expect(created?.role).toBe("observateur");
  28  | 
  29  |     // The list is still correct after a reload.
  30  |     await page.reload();
  31  |     await page.getByRole("tab", { name: "Permissions", exact: true }).click();
  32  |     await expect(page.getByRole("combobox", { name: `Role of ${OBSERVER.username}` })).toBeVisible();
  33  |     expect(problems.filter((p) => !/Failed to load resource/.test(p))).toEqual([]);
  34  |   });
  35  | 
  36  |   test("refuses a duplicate user name with a clear message", async ({ page }) => {
  37  |     await uiLogin(page);
  38  |     await page.getByRole("tab", { name: "Permissions", exact: true }).click();
  39  |     await page.getByRole("textbox", { name: "Username" }).fill(OBSERVER.username);
  40  |     await page.getByRole("textbox", { name: /Password/ }).fill(OBSERVER.password);
  41  |     await page.getByRole("button", { name: "Create", exact: true }).first().click();
  42  |     await expect(page.getByText(/already exists|Creation failed/i).first()).toBeVisible();
  43  |   });
  44  | 
  45  |   test("the new user signs in and does not see administrative actions", async ({ page }) => {
  46  |     await uiLogin(page, OBSERVER.username, OBSERVER.password);
  47  |     await expect(page.getByRole("button", { name: "Create VM" })).toHaveCount(0);
  48  |     await expect(page.getByRole("button", { name: "Create container" })).toHaveCount(0);
  49  |   });
  50  | 
  51  |   test("the backend refuses administrative calls from the read-only user (not just the UI)", async ({ request }) => {
  52  |     const token = await apiLogin(request, OBSERVER.username, OBSERVER.password);
  53  |     const h = { Authorization: `Bearer ${token}` };
  54  |     const denied: Array<[string, () => Promise<{ status(): number }>]> = [
  55  |       ["create VM", () => request.post("/vms", { headers: h, data: { name: `${PREFIX}denied`, vcpu: 1, memory_mb: 256, disks: [{ size_gb: 1 }], network: "default", username: "u", password: "pppp" } })],
  56  |       ["create user", () => request.post("/auth/users", { headers: h, data: { username: `${PREFIX}x`, password: "pppp", role: "admin" } })],
  57  |       ["delete user", () => request.delete(`/auth/users/${ADMIN.username}`, { headers: h })],
  58  |       ["create network", () => request.post("/networks", { headers: h, data: { name: `${PREFIX}n`, mode: "isole" } })],
  59  |       ["create storage pool", () => request.post("/storage", { headers: h, data: { name: `${PREFIX}p`, type: "dir" } })],
  60  |       ["update profile", () => request.put("/host/profile", { headers: h, data: { profil: "standard" } })],
  61  |       ["SSO config", () => request.get("/auth/sso/config", { headers: h })],
  62  |       ["host shell ticket", () => request.post("/host/terminal-ticket", { headers: h })],
  63  |     ];
  64  |     for (const [name, call] of denied) {
  65  |       const res = await call();
  66  |       expect(res.status(), `${name} must be forbidden for an observer`).toBe(403);
  67  |     }
  68  |     // Read access stays available.
  69  |     expect((await request.get("/vms", { headers: h })).ok()).toBeTruthy();
  70  |   });
  71  | 
  72  |   test("an observer cannot read notification channel secrets", async ({ request }) => {
  73  |     const adminToken = await apiLogin(request);
  74  |     const ah = { Authorization: `Bearer ${adminToken}` };
  75  |     const created = await request.post("/notifications/channels", { headers: ah, data: { name: `${PREFIX}hook`, type: "webhook", config: { url: "https://example.invalid/hook?token=s3cret" }, events: [] } });
  76  |     expect(created.ok()).toBeTruthy();
  77  |     const { id } = await created.json();
  78  |     try {
  79  |       const obsToken = await apiLogin(request, OBSERVER.username, OBSERVER.password);
  80  |       const r = await request.get("/notifications/channels", { headers: { Authorization: `Bearer ${obsToken}` } });
  81  |       expect(JSON.stringify(await r.json())).not.toContain("s3cret");
  82  |     } finally {
  83  |       await request.delete(`/notifications/channels/${id}`, { headers: ah });
  84  |     }
  85  |   });
  86  | 
  87  |   test("an administrator deletes the user after confirming", async ({ page, request }) => {
  88  |     await uiLogin(page);
  89  |     await page.getByRole("tab", { name: "Permissions", exact: true }).click();
  90  |     page.once("dialog", (d) => {
  91  |       expect(d.message()).toContain(OBSERVER.username);
  92  |       void d.accept();
  93  |     });
  94  |     await page.getByRole("combobox", { name: `Role of ${OBSERVER.username}` }).locator("xpath=..").getByRole("button").click();
> 95  |     await expect(page.getByRole("combobox", { name: `Role of ${OBSERVER.username}` })).toHaveCount(0);
      |                                                                                        ^ Error: expect(locator).toHaveCount(expected) failed
  96  |     const token = await apiLogin(request);
  97  |     const users = await (await request.get("/auth/users", { headers: { Authorization: `Bearer ${token}` } })).json();
  98  |     expect(users.map((u: { username: string }) => u.username)).not.toContain(OBSERVER.username);
  99  |     const login = await request.post("/auth/login", { form: { username: OBSERVER.username, password: OBSERVER.password } });
  100 |     expect(login.status()).toBe(401);
  101 |   });
  102 | });
  103 | 
```