import { expect, test, uiLogin } from "../support/fixtures";

// The graphical console's key combinations and "Type text", over a real VNC connection when the host can run the
// VM named by E2E_CONSOLE_VM (created by the caller, e.g. an emulated VM without OS): the keys go through noVNC
// to QEMU. Skipped when no such VM is given.
const VM = process.env.E2E_CONSOLE_VM;

test("console: key combinations and typing text into the VM", async ({ page }) => {
  test.skip(!VM, "E2E_CONSOLE_VM names no running VM");
  await uiLogin(page);
  await page.goto(`/vm/${VM}?tab=console`);
  const main = page.getByRole("main");
  await expect(main.getByRole("status").filter({ hasText: /^Connected$/ })).toBeVisible({ timeout: 30_000 });

  await main.getByRole("button", { name: "Keys" }).click();
  await page.getByRole("menuitem", { name: "Ctrl+Alt+F2" }).click();
  await expect(page.getByRole("menu")).toHaveCount(0);

  await main.getByRole("button", { name: "Type text" }).click();
  const bar = main.getByRole("group", { name: "Type text" });
  await bar.getByLabel("Text to type into the VM").fill("root\npassw0rd!");
  await bar.getByRole("button", { name: "Type into the VM" }).click();
  // The panel closes once every key is sent; the connection stays up.
  await expect(bar).toHaveCount(0);
  await expect(main.getByRole("status").filter({ hasText: /^Connected$/ })).toBeVisible();
});
