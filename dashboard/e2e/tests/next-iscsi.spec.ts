import { expect, goTo, test, uiLogin } from "../support/fixtures";

// The iSCSI pool form (the test machine has no iSCSI target: /storage/support and the creation answer are simulated;
// the real path, a LIO target and a VM booting from a LUN, was checked on the development machine).
test("the iSCSI pool form shows the initiator name to allow and checks the target name", async ({ page }) => {
  await page.route("**/storage/support", (route) =>
    route.fulfill({ json: { nfs: "ok", zfs: "not_installed", iscsi: "ok", iscsi_initiator: "iqn.1993-08.org.debian:01:e2e0test" } }));
  let sent: Record<string, unknown> | null = null;
  await page.route("**/storage", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    sent = route.request().postDataJSON();
    return route.fulfill({ status: 502, json: { detail: "Could not log in to the iSCSI target: check the portal address" } });
  });
  await uiLogin(page);
  await goTo(page, "Storage");
  await page.getByRole("main").getByRole("button", { name: "Create a pool" }).click();
  const drawer = page.getByRole("dialog", { name: "Create a pool" });
  await drawer.getByRole("button", { name: "iSCSI", exact: true }).click();
  await expect(drawer.getByText("iqn.1993-08.org.debian:01:e2e0test")).toBeVisible();
  const create = drawer.getByRole("button", { name: "Create a pool" });
  // Nothing typed: the request is not sent, and each required field says so.
  await create.click();
  await expect(drawer.getByText("Required.")).toHaveCount(3); // name, portal, target
  expect(sent).toBeNull();
  await drawer.getByLabel("Pool name").fill("nas");
  await drawer.getByLabel("Storage server (portal)").fill("192.168.1.20");
  await drawer.getByLabel("Target name (IQN)").fill("vms");
  await expect(drawer.getByText("Expected an IQN such as")).toBeVisible();
  await drawer.getByLabel("Target name (IQN)").fill("iqn.2005-10.org.freenas.ctl:vms");
  // CHAP: the user and the password go together, in either order.
  const chap = drawer.getByRole("group", { name: "CHAP authentication (optional)" });
  await chap.getByLabel("Password").fill("s3cret-chap");
  await create.click();
  await expect(chap.getByText("CHAP needs the user and the password together.")).toBeVisible();
  expect(sent).toBeNull();
  await chap.getByLabel("User").fill("hyper");
  await expect(chap.getByText("CHAP needs the user and the password together.")).toHaveCount(0);
  await create.click();
  await expect(page.getByText(/Could not log in to the iSCSI target/).first()).toBeVisible();
  expect(sent).toMatchObject({ type: "iscsi", iscsi_host: "192.168.1.20", iscsi_port: 3260, iscsi_target: "iqn.2005-10.org.freenas.ctl:vms", chap_user: "hyper", chap_password: "s3cret-chap" });
});

test("the pool form asks for what each kind needs: NFS server and path together, ZFS on this host only", async ({ page }) => {
  await page.route("**/storage/support", (route) => route.fulfill({ json: { nfs: "ok", zfs: "ok", iscsi: "ok", iscsi_initiator: null } }));
  let sent: Record<string, unknown> | null = null;
  await page.route("**/storage", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    sent = route.request().postDataJSON();
    return route.fulfill({ status: 502, json: { detail: "mount.nfs: access denied by server" } });
  });
  await uiLogin(page);
  await goTo(page, "Storage");
  await page.getByRole("main").getByRole("button", { name: "Create a pool" }).click();
  const drawer = page.getByRole("dialog", { name: "Create a pool" });
  const create = drawer.getByRole("button", { name: "Create a pool" });
  await expect(drawer.getByText("Every field is required except")).toBeVisible();
  await drawer.getByRole("button", { name: "NFS share" }).click();
  // Examples read as examples, never as values already filled in.
  await expect(drawer.getByLabel("Pool name")).toHaveAttribute("placeholder", "e.g. nas-vms");
  await drawer.getByLabel("Pool name").fill("n");
  await drawer.getByLabel("NFS server").fill("192.168.1.10");
  await create.click();
  await expect(drawer.getByText("Letters, digits and hyphens, 2 to 63 characters, starting")).toBeVisible();
  await expect(drawer.getByText("Required.")).toHaveCount(1); // the exported path: the server alone is not a share
  await drawer.getByLabel("Pool name").fill("nas-vms");
  await drawer.getByLabel("Exported path").fill("srv/share");
  await expect(drawer.getByText("An absolute path starting with /")).toBeVisible();
  expect(sent).toBeNull();
  await drawer.getByLabel("Exported path").fill("/srv/share");
  await create.click();
  await expect(page.getByText(/access denied by server/).first()).toBeVisible();
  expect(sent).toMatchObject({ type: "netfs", name: "nas-vms", nfs_host: "192.168.1.10", nfs_export_path: "/srv/share", nfs_version: "4.2" });
});
