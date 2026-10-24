import { apiLogin, expect, PREFIX, test, uiLogin } from "../support/fixtures";

// A backup store on the Backups page, against the real backend (docs/design/backup-store.md): made in a directory of
// a storage pool of this node, offered as a backup job's destination, then removed. Backing up into a store runs on a
// real QEMU in tests/test_backup_store_qemu.py.
const stamp = Date.now().toString().slice(-6);
const POOL = `${PREFIX}bst-${stamp}`;
const STORE = `${PREFIX}store-${stamp}`;

test("backup stores: one made on a pool, offered as a destination, then removed", async ({ page, request }) => {
  const token = await apiLogin(request);
  const auth = () => ({ Authorization: `Bearer ${token}` });
  const created = await request.post("/storage", { headers: auth(), data: { name: POOL, type: "dir" } });
  expect(created.ok(), await created.text()).toBe(true);
  try {
    await uiLogin(page);
    await page.goto("/datacenter?tab=backups");
    const main = page.getByRole("main");
    await main.getByRole("button", { name: "Add a store" }).click();
    const drawer = page.getByRole("dialog", { name: "Add a store" });
    await drawer.getByLabel("Name", { exact: true }).fill("bad/name");
    await expect(drawer.getByText("Letters, digits, '.', '-' and '_', 63 characters at most")).toBeVisible();
    await drawer.getByLabel("Name", { exact: true }).fill(STORE);
    await drawer.getByLabel("Storage pool").selectOption(POOL);
    await drawer.getByRole("button", { name: "Create the store" }).click();
    await expect(page.getByText("Backup store ready")).toBeVisible();

    const row = main.getByRole("row", { name: new RegExp(`^${STORE} `) }); // not the encrypted test's store
    await expect(row).toContainText(`${POOL}/hyperlite-store`);
    await expect(row).toContainText("Never");
    const listed = (await (await request.get("/backup-stores", { headers: auth() })).json()) as Array<{ name: string; available: boolean; backups: number }>;
    expect(listed.find((s) => s.name === STORE)).toMatchObject({ available: true, backups: 0 });

    // A backup job may back up into it, in place of a directory.
    await main.getByRole("button", { name: "Add a backup job" }).click();
    const job = page.getByRole("dialog", { name: "Add a backup job" });
    await job.getByLabel("Destination").selectOption({ label: `Store ${STORE}` });
    await expect(job.getByLabel("Backup directory")).toHaveCount(0);
    await job.getByRole("button", { name: "Cancel" }).click();

    await row.getByRole("button", { name: `Remove the store ${STORE}` }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText("Backup store removed")).toBeVisible();
    await expect(main.getByRole("row", { name: new RegExp(`^${STORE} `) })).toHaveCount(0);
  } finally {
    await request.delete(`/backup-stores/${STORE}`, { headers: auth() }).catch(() => {});
    await request.delete(`/storage/${POOL}?confirm=true&detach=true`, { headers: auth() }).catch(() => {});
  }
});

test("an encrypted store: its keys are shown once and must be kept before the drawer closes", async ({ page, request }) => {
  const token = await apiLogin(request);
  const auth = () => ({ Authorization: `Bearer ${token}` });
  const pool = `${POOL}-enc`;
  const store = `${STORE}-enc`;
  const created = await request.post("/storage", { headers: auth(), data: { name: pool, type: "dir" } });
  expect(created.ok(), await created.text()).toBe(true);
  try {
    await uiLogin(page);
    await page.goto("/datacenter?tab=backups");
    const main = page.getByRole("main");
    await main.getByRole("button", { name: "Add a store" }).click();
    const drawer = page.getByRole("dialog", { name: "Add a store" });
    await drawer.getByLabel("Name", { exact: true }).fill(store);
    await drawer.getByLabel("Storage pool").selectOption(pool);
    await drawer.getByLabel("Encrypt this store").check();
    await drawer.getByLabel("Master recovery key").selectOption("generate");
    await drawer.getByRole("button", { name: "Create the store" }).click();

    const keys = page.getByRole("dialog", { name: `Keep the keys of ${store}` });
    await expect(keys.getByText("Hyperlite shows these keys now and never again")).toBeVisible({ timeout: 30_000 });
    await expect(keys.getByLabel("Store key")).toHaveValue(/^[A-Z2-7]{4}(-[A-Z2-7]{1,4})+$/);
    await expect(keys.getByRole("img", { name: "QR code of the store key" })).toBeVisible();
    await expect(keys.getByRole("button", { name: "Download the master recovery key" })).toBeVisible();
    await expect(keys.getByRole("button", { name: "Done" })).toBeDisabled();
    await keys.getByLabel("I saved these keys somewhere outside this cluster").check();
    await keys.getByRole("button", { name: "Done" }).click();
    await expect(keys).toHaveCount(0);

    const row = main.getByRole("row", { name: new RegExp(store) });
    await expect(row).toContainText("Encrypted");
    const one = await (await request.get(`/backup-stores/${store}`, { headers: auth() })).json();
    expect(one).toMatchObject({ encrypted: true, master_key: true, available: true });
    expect(one.key_code).toBeUndefined();
  } finally {
    await request.delete(`/backup-stores/${store}`, { headers: auth() }).catch(() => {});
    await request.delete(`/storage/${pool}?confirm=true&detach=true`, { headers: auth() }).catch(() => {});
  }
});
