import { apiLogin, expect, PREFIX, test, uiLogin } from "../support/fixtures";

const stamp = Date.now().toString().slice(-5);
const NAME = `${PREFIX}net-${stamp}`;
const third = 100 + (Number(stamp) % 100);
const SUBNET = `192.168.${third}`;

test.describe.configure({ mode: "serial" });

test.afterAll(async ({ request }) => {
  const token = await apiLogin(request);
  await request.delete(`/networks/${NAME}?confirm=true`, { headers: { Authorization: `Bearer ${token}` } });
});

async function openNetworkTab(page: import("@playwright/test").Page) {
  await uiLogin(page);
  await page.getByRole("tab", { name: "Network", exact: true }).click();
}

test.describe("Virtual networks (real libvirt backend)", () => {
  test("creates an isolated network and it survives a reload", async ({ page, request, problems }) => {
    await openNetworkTab(page);
    await page.getByRole("button", { name: "Create a virtual network" }).click();
    await page.getByRole("textbox", { name: "Name", exact: true }).fill(NAME);
    await page.getByRole("textbox", { name: /Gateway/ }).fill(`${SUBNET}.1`);
    await page.getByRole("textbox", { name: "DHCP start" }).fill(`${SUBNET}.10`);
    await page.getByRole("textbox", { name: "DHCP end" }).fill(`${SUBNET}.50`);
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByRole("button", { name: new RegExp(`^${NAME}`) })).toBeVisible();

    const token = await apiLogin(request);
    const nets = await (await request.get("/networks", { headers: { Authorization: `Bearer ${token}` } })).json();
    const created = nets.find((n: { nom: string }) => n.nom === NAME);
    expect(created, "network exists in libvirt").toBeTruthy();

    await page.reload();
    await page.getByRole("tab", { name: "Network", exact: true }).click();
    await expect(page.getByRole("button", { name: new RegExp(`^${NAME}`) })).toBeVisible();
    expect(problems.filter((p) => !/Failed to load resource/.test(p))).toEqual([]);
  });

  test("refuses a duplicate network name", async ({ page }) => {
    await openNetworkTab(page);
    await page.getByRole("button", { name: "Create a virtual network" }).click();
    await page.getByRole("textbox", { name: "Name", exact: true }).fill(NAME);
    await page.getByRole("textbox", { name: /Gateway/ }).fill(`${SUBNET}.1`);
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByText(/already|exists|failed/i).first()).toBeVisible();
  });

  test("refuses an invalid bridge interface name", async ({ page }) => {
    await openNetworkTab(page);
    await page.getByRole("button", { name: "Create a virtual network" }).click();
    await page.getByRole("textbox", { name: "Name", exact: true }).fill(`${PREFIX}br-${stamp}`);
    await page.getByRole("combobox", { name: "Network mode" }).selectOption({ label: "Bridge to an existing physical network" });
    await page.getByRole("textbox", { name: /Host bridge name/ }).fill("bad name; rm -rf /");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByText(/invalid|failed/i).first()).toBeVisible();
    await expect(page.getByRole("button", { name: new RegExp(`^${PREFIX}br-${stamp}`) })).toHaveCount(0);
  });

  test("shows the network details and deletes it after a confirmation", async ({ page, request }) => {
    await openNetworkTab(page);
    await page.getByRole("button", { name: new RegExp(`^${NAME}`) }).click();
    await expect(page.getByText(/Active DHCP leases/)).toBeVisible();
    await page.getByRole("button", { name: `Delete network ${NAME}` }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText(NAME);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("button", { name: new RegExp(`^${NAME}`) }).first()).toBeVisible();
    // Escape also cancels and the focus returns to the button that opened the dialog.
    await page.getByRole("button", { name: `Delete network ${NAME}` }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    await expect(page.getByRole("button", { name: `Delete network ${NAME}` })).toBeFocused();

    await page.getByRole("button", { name: `Delete network ${NAME}` }).click();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
    await expect(page.getByRole("button", { name: new RegExp(`^${NAME}`) })).toHaveCount(0);

    const token = await apiLogin(request);
    const nets = await (await request.get("/networks", { headers: { Authorization: `Bearer ${token}` } })).json();
    expect(nets.map((n: { nom: string }) => n.nom)).not.toContain(NAME);
  });
});
