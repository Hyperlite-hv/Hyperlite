import { apiLogin, expect, PREFIX, test, uiLogin } from "../support/fixtures";
import type { APIRequestContext, Page } from "@playwright/test";

const stamp = Date.now().toString().slice(-6);
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
});

async function listNames(request: APIRequestContext, path: string, key: string) {
  const res = await request.get(path, { headers: auth() });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as Array<Record<string, string>>).map((x) => x[key]);
}

/** Opens the dialog, checks it names the resource, cancels once (nothing changes), then confirms. */
async function confirmDeletion(page: Page, trigger: () => Promise<void>, resource: string, confirmLabel: RegExp) {
  await trigger();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText(resource);
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  await trigger();
  await page.getByRole("alertdialog").getByRole("button", { name: confirmLabel }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
}

test.describe("Destructive actions always ask for a confirmation naming the resource", () => {
  test("user group: cancel keeps it, confirm removes it (UI and backend)", async ({ page, request }) => {
    const name = `${PREFIX}group-${stamp}`;
    await uiLogin(page);
    await page.getByRole("tab", { name: "Permissions", exact: true }).click();
    await page.getByRole("textbox", { name: /Group name/ }).fill(name);
    await page.getByRole("button", { name: "Create", exact: true }).nth(1).click();
    await expect(page.getByRole("button", { name: `Delete group ${name}` })).toBeVisible();
    expect(await listNames(request, "/groups", "name")).toContain(name);

    const trigger = async () => {
      await page.getByRole("button", { name: `Delete group ${name}` }).click();
    };
    await confirmDeletion(page, trigger, name, /^Delete$/);
    await expect(page.getByRole("button", { name: `Delete group ${name}` })).toHaveCount(0);
    expect(await listNames(request, "/groups", "name")).not.toContain(name);
  });

  test("notification channel: confirmation, then removal from the backend", async ({ page, request }) => {
    const name = `${PREFIX}hook-${stamp}`;
    const created = await request.post("/notifications/channels", { headers: auth(), data: { name, type: "webhook", config: { url: "https://example.invalid/hook" }, events: [] } });
    expect(created.ok()).toBeTruthy();
    await uiLogin(page);
    await page.getByRole("tab", { name: "Notifications", exact: true }).click();
    await expect(page.getByRole("button", { name: `Delete channel ${name}` })).toBeVisible();
    const trigger = async () => {
      await page.getByRole("button", { name: `Delete channel ${name}` }).click();
    };
    await confirmDeletion(page, trigger, name, /Confirm/);
    await expect(page.getByRole("button", { name: `Delete channel ${name}` })).toHaveCount(0);
    expect(await listNames(request, "/notifications/channels", "name")).not.toContain(name);
  });

  test("ISO image: upload, list, cancel deletion, confirm deletion", async ({ page, request }) => {
    const file = `${PREFIX}iso-${stamp}.iso`;
    await uiLogin(page);
    await page.getByRole("tab", { name: "Storage", exact: true }).click();
    await page.getByLabel("ISO image file").setInputFiles({ name: file, mimeType: "application/octet-stream", buffer: Buffer.alloc(4096, 1) });
    await expect(page.getByRole("button", { name: `Delete ISO ${file}` })).toBeVisible();
    expect(await listNames(request, "/isos", "nom")).toContain(file);
    await page.reload();
    await page.getByRole("tab", { name: "Storage", exact: true }).click();
    await expect(page.getByRole("button", { name: `Delete ISO ${file}` })).toBeVisible();

    const trigger = async () => {
      await page.getByRole("button", { name: `Delete ISO ${file}` }).click();
    };
    await confirmDeletion(page, trigger, file, /^Delete$/);
    await expect(page.getByRole("button", { name: `Delete ISO ${file}` })).toHaveCount(0);
    expect(await listNames(request, "/isos", "nom")).not.toContain(file);
  });
});

test.afterAll(async ({ request }) => {
  // Remove anything left behind by a failed run: only resources created by these tests (prefix).
  for (const [path, key, del] of [
    ["/groups", "name", "/groups/"],
    ["/isos", "nom", "/isos/"],
  ] as const) {
    const res = await request.get(path, { headers: auth() });
    if (!res.ok()) continue;
    for (const item of (await res.json()) as Array<Record<string, string>>) {
      if (String(item[key]).startsWith(PREFIX)) await request.delete(`${del}${path === "/groups" ? item.id : item[key]}${path === "/isos" ? "?confirm=true" : ""}`, { headers: auth() });
    }
  }
  const ch = await request.get("/notifications/channels", { headers: auth() });
  if (ch.ok()) for (const c of (await ch.json()) as Array<{ id: number; name: string }>) if (c.name.startsWith(PREFIX)) await request.delete(`/notifications/channels/${c.id}`, { headers: auth() });
});
