import { apiLogin, expect, goTo, PREFIX, test, uiLogin } from "../support/fixtures";

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
  await goTo(page, "Network");
}

// The create form is a side drawer opened by the page primary.
async function openCreate(page: import("@playwright/test").Page) {
  await page.getByRole("main").getByRole("button", { name: "Create a network" }).click();
  return page.getByRole("dialog", { name: "Create a network" });
}

test.describe("Virtual networks (real libvirt backend)", () => {
  test("creates an isolated network and it survives a reload", async ({ page, request, problems }) => {
    await openNetworkTab(page);
    const form = await openCreate(page);
    await form.getByRole("textbox", { name: "Name", exact: true }).fill(NAME);
    await form.getByRole("textbox", { name: /Gateway/ }).fill(`${SUBNET}.1`);
    await form.getByRole("textbox", { name: "DHCP start" }).fill(`${SUBNET}.10`);
    await form.getByRole("textbox", { name: "DHCP end" }).fill(`${SUBNET}.50`);
    await form.getByRole("button", { name: "Create a network", exact: true }).click();
    await expect(page.getByRole("main").getByRole("row", { name: new RegExp(NAME) })).toBeVisible({ timeout: 30_000 });

    const token = await apiLogin(request);
    const nets = await (await request.get("/networks", { headers: { Authorization: `Bearer ${token}` } })).json();
    const created = nets.find((n: { nom: string }) => n.nom === NAME);
    expect(created, "network exists in libvirt").toBeTruthy();

    await page.reload();
    await expect(page.getByRole("main").getByRole("row", { name: new RegExp(NAME) })).toBeVisible();
    expect(problems.filter((p) => !/Failed to load resource/.test(p))).toEqual([]);
  });

  test("refuses a duplicate network name", async ({ page }) => {
    await openNetworkTab(page);
    const form = await openCreate(page);
    await form.getByRole("textbox", { name: "Name", exact: true }).fill(NAME);
    await form.getByRole("textbox", { name: /Gateway/ }).fill(`${SUBNET}.1`);
    await form.getByRole("button", { name: "Create a network", exact: true }).click();
    await expect(page.getByText(/already|exists|failed/i).first()).toBeVisible();
  });

  test("offers only the host's interfaces for a bridge, and the API refuses any other name", async ({ page, request }) => {
    await openNetworkTab(page);
    const form = await openCreate(page);
    await form.getByRole("textbox", { name: "Name", exact: true }).fill(`${PREFIX}br-${stamp}`);
    await form.getByRole("combobox", { name: "Network mode" }).selectOption({ label: "Bridge to an existing physical network" });
    // a list of the host's interfaces now, not free text; libvirt's own bridges are never offered
    const iface = form.getByRole("combobox", { name: /Host bridge name/ });
    await expect(iface).toBeVisible();
    await expect(iface.locator("option").first()).not.toHaveText(/Loading/, { timeout: 15_000 });
    expect((await iface.locator("option").allInnerTexts()).some((o) => /^virbr/.test(o))).toBe(false);
    // the check that matters is on the server: an invalid or unknown name is refused, nothing is created
    const token = await apiLogin(request);
    for (const bad of ["bad name; rm -rf /", "virbr0", "no-such-if0"]) {
      const r = await request.post("/networks", { headers: { Authorization: `Bearer ${token}` }, data: { name: `${PREFIX}br-${stamp}`, mode: "bridge", bridge_name: bad } });
      expect(r.status(), bad).toBe(422);
    }
    await page.reload();
    await expect(page.getByRole("main").getByRole("row", { name: new RegExp(`${PREFIX}br-${stamp}`) })).toHaveCount(0);
  });

  test("shows the network details and deletes it after a confirmation", async ({ page, request }) => {
    await openNetworkTab(page);
    await page.getByRole("button", { name: `Details of ${NAME}` }).click();
    await expect(page.getByText(/Active DHCP leases/)).toBeVisible();
    await page.getByRole("button", { name: `Delete network ${NAME}` }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText(NAME);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("main").getByRole("row", { name: new RegExp(NAME) }).first()).toBeVisible();
    // Escape also cancels and the focus returns to the button that opened the dialog.
    await page.getByRole("button", { name: `Delete network ${NAME}` }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    await expect(page.getByRole("button", { name: `Delete network ${NAME}` })).toBeFocused();

    await page.getByRole("button", { name: `Delete network ${NAME}` }).click();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete", exact: true }).click();
    await expect(page.getByRole("main").getByRole("row", { name: new RegExp(NAME) })).toHaveCount(0, { timeout: 30_000 });

    const token = await apiLogin(request);
    const nets = await (await request.get("/networks", { headers: { Authorization: `Bearer ${token}` } })).json();
    expect(nets.map((n: { nom: string }) => n.nom)).not.toContain(NAME);
  });
});
