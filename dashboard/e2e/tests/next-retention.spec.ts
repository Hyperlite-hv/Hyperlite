import { apiLogin, expect, test, uiLogin } from "../support/fixtures";

// How long the users' addresses are kept (GDPR, #448 F4): set from Activity > Audit log > Retention, checked by the
// form, saved for the cluster, and put back as it was.
let admin = "";
let before: { access_log_days: number; audit_days: number } | null = null;
const auth = () => ({ Authorization: `Bearer ${admin}` });

test.describe.configure({ mode: "serial", timeout: 120_000 });
test.beforeAll(async ({ request }) => {
  admin = await apiLogin(request);
  before = await (await request.get("/audit/retention", { headers: auth() })).json();
});
test.afterAll(async ({ request }) => {
  if (before) await request.put("/audit/retention", { headers: auth(), data: before });
});

test("an administrator sets how long the access log and the audit log are kept", async ({ page, request }) => {
  await uiLogin(page, undefined, undefined, "/datacenter?tab=journal");
  await page.getByRole("button", { name: "Retention" }).click();
  const dialog = page.getByRole("dialog", { name: "How long addresses are kept" });
  const access = dialog.getByLabel("Access log");
  const audit = dialog.getByLabel("Audit log");
  await expect(access).toHaveValue(String(before?.access_log_days));
  const save = dialog.getByRole("button", { name: "Save" });
  await access.fill("5000");
  await expect(save).toBeDisabled();
  await access.fill("14");
  await audit.fill("180");
  await save.click();
  await expect(dialog).toBeHidden();
  const now = await (await request.get("/audit/retention", { headers: auth() })).json();
  expect(now.access_log_days).toBe(14);
  expect(now.audit_days).toBe(180);
});
