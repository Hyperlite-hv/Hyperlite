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
  await drawer.getByLabel("Pool name").fill("nas");
  await drawer.getByLabel("Storage server (portal)").fill("192.168.1.20");
  await drawer.getByLabel("Target name (IQN)").fill("vms");
  await expect(drawer.getByText("Expected an IQN such as")).toBeVisible();
  await expect(create).toBeDisabled();
  await drawer.getByLabel("Target name (IQN)").fill("iqn.2005-10.org.freenas.ctl:vms");
  await drawer.getByLabel("CHAP user (optional)").fill("hyper");
  await expect(create).toBeDisabled(); // a CHAP user needs its password
  await drawer.getByLabel("CHAP password").fill("s3cret-chap");
  await create.click();
  await expect(page.getByText(/Could not log in to the iSCSI target/).first()).toBeVisible();
  expect(sent).toMatchObject({ type: "iscsi", iscsi_host: "192.168.1.20", iscsi_port: 3260, iscsi_target: "iqn.2005-10.org.freenas.ctl:vms", chap_user: "hyper", chap_password: "s3cret-chap" });
});
