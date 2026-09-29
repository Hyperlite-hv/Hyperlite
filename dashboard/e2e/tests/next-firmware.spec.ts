import type { Page, Route } from "@playwright/test";
import { ADMIN, apiLogin, expect, PREFIX, test } from "../support/fixtures";

// VM firmware (BIOS, UEFI, UEFI + Secure Boot + TPM 2.0). The wizard is checked with the host's answer stubbed
// (both "available" and "missing"), and the creation request is stopped before it reaches the server. A real
// Secure Boot VM is then created, started and deleted on the real backend when this host has OVMF and swtpm (the CI
// installs them). The XML and the refusals are covered by tests/test_firmware.py.
test.describe.configure({ mode: "serial", timeout: 180_000 });

const stamp = Date.now().toString().slice(-6);
const NAME = `${PREFIX}uefi-${stamp}`;
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

async function open(page: Page, lang = "en") {
  await page.addInitScript((l) => { if (!localStorage.getItem("hyperlite-ui")) { localStorage.setItem("hyperlite-ui", "next"); localStorage.setItem("hyperlite-next-lang", l); } }, lang);
  await page.goto("/");
  await page.getByLabel(/Username|Nom d.utilisateur/).fill(ADMIN.username);
  await page.getByLabel(/^(Password|Mot de passe)$/).fill(ADMIN.password);
  await page.getByRole("button", { name: /Sign in|Se connecter/ }).click();
  await expect(page.locator(".nx-root")).toBeVisible({ timeout: 30_000 });
}

async function wizardToAdvanced(page: Page, name: string) {
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("menuitem", { name: "Virtual machine" }).click();
  const dlg = page.getByRole("dialog");
  const next = () => dlg.getByRole("button", { name: "Next" }).click();
  await next(); // source: Debian cloud image
  await dlg.getByRole("textbox", { name: "VM name" }).fill(name);
  await dlg.getByRole("textbox", { name: "User" }).fill("tester");
  await dlg.getByRole("textbox", { name: "Password" }).fill("Testpass1");
  await next(); // identity
  await next(); // placement
  await next(); // compute
  await next(); // storage
  await dlg.getByRole("radio", { name: /hyperlite-isolated/ }).check();
  await next(); // network -> advanced
  return dlg;
}

test("the wizard offers Secure Boot when the host has it and sends the choice", async ({ page }) => {
  await page.route(/\/host\/firmware$/, (route) => json(route, { uefi: true, uefi_secure: true, raison: null }));
  let posted: Record<string, unknown> | null = null;
  await page.route(/\/vms$/, (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posted = route.request().postDataJSON();
    return json(route, { detail: "Stopped by the test" }, 422);
  });
  await open(page);
  const dlg = await wizardToAdvanced(page, "e2e-fw-wizard");
  const select = dlg.getByLabel("Firmware");
  // A Linux cloud image keeps the BIOS it always had unless asked otherwise.
  await expect(select.locator("option", { hasText: "Automatic (recommended): BIOS (legacy)" })).toHaveCount(1);
  await select.selectOption("uefi_secure");
  await expect(dlg.getByText(/^What Windows 11 requires/)).toBeVisible();
  await dlg.getByRole("button", { name: "Next" }).click(); // review
  await expect(dlg.getByRole("region", { name: "Advanced" })).toContainText("UEFI + Secure Boot + TPM 2.0");
  await dlg.getByRole("button", { name: "Create the VM" }).click();
  await expect(dlg.getByText("Stopped by the test")).toBeVisible();
  expect(posted).toMatchObject({ name: "e2e-fw-wizard", firmware: "uefi_secure" });
});

test("without swtpm the Secure Boot choice is disabled and the wizard says what to install", async ({ page }) => {
  const reason = "No software TPM on this host: install the swtpm and swtpm-tools packages";
  await page.route(/\/host\/firmware$/, (route) => json(route, { uefi: true, uefi_secure: false, raison: reason }));
  await open(page, "fr");
  await page.getByRole("button", { name: "Créer" }).click();
  await page.getByRole("menuitem", { name: "Machine virtuelle" }).click();
  const dlg = page.getByRole("dialog");
  for (let i = 0; i < 6; i += 1) {
    if (i === 1) {
      await dlg.getByRole("textbox", { name: "Nom de la VM" }).fill("e2e-fw-missing");
      await dlg.getByRole("textbox", { name: "Utilisateur" }).fill("tester");
      await dlg.getByRole("textbox", { name: "Mot de passe" }).fill("Testpass1");
    }
    if (i === 5) await dlg.getByRole("radio", { name: /hyperlite-isolated/ }).check();
    await dlg.getByRole("button", { name: "Suivant" }).click();
  }
  const select = dlg.getByLabel("Micrologiciel");
  // toBeDisabled() does not look at an <option>'s own attribute.
  await expect(select.locator("option[value='uefi_secure']")).toHaveAttribute("disabled", "");
  await expect(select.locator("option[value='uefi']")).not.toHaveAttribute("disabled", "");
  await expect(dlg.getByText(`Indisponible sur cet hôte : ${reason}.`)).toBeVisible();
});

test("a real Secure Boot VM starts with its TPM, refuses a live snapshot, and is deleted with its NVRAM", async ({ page, request }) => {
  const token = await apiLogin(request);
  const auth = { Authorization: `Bearer ${token}` };
  const support = (await (await request.get("/host/firmware", { headers: auth })).json()) as { uefi_secure: boolean; raison: string | null };
  test.skip(!support.uefi_secure, `This host cannot build a Secure Boot VM: ${support.raison}`);
  try {
    const res = await request.post("/vms", { headers: auth, data: { name: NAME, vcpu: 1, memory_mb: 512, disks: [{ size_gb: 3 }], network: "hyperlite-isolated", username: "tester", password: "Testpass1", firmware: "uefi_secure" } });
    expect(res.ok(), await res.text()).toBe(true);
    expect(((await res.json()) as { firmware: string }).firmware).toBe("uefi_secure");
    const start = await request.post(`/vms/${NAME}/start`, { headers: auth });
    expect(start.ok(), await start.text()).toBe(true);

    const snap = await request.post(`/vms/${NAME}/snapshots`, { headers: auth, data: { name: "live" } });
    expect(snap.status()).toBe(409);
    expect(await snap.text()).toContain("Shut it down");

    await open(page);
    await page.goto(`/vm/${NAME}?tab=summary`);
    const main = page.getByRole("main");
    await expect(main.getByText("UEFI + Secure Boot + TPM 2.0")).toBeVisible({ timeout: 20_000 });
    await page.goto(`/vm/${NAME}?tab=snapshots`);
    await expect(main.getByText(/^This VM uses UEFI firmware/)).toBeVisible({ timeout: 20_000 });
    await expect(main.getByRole("button", { name: "Create a snapshot" })).toBeDisabled();

    await request.post(`/vms/${NAME}/stop?force=true`, { headers: auth });
    await expect.poll(async () => ((await (await request.get(`/vms/${NAME}`, { headers: auth })).json()) as { etat: string }).etat, { timeout: 60_000 }).toBe("arrete");
    await page.reload();
    await expect(main.getByRole("button", { name: "Create a snapshot" })).toBeEnabled({ timeout: 20_000 });
  } finally {
    await request.post(`/vms/${NAME}/stop?force=true`, { headers: auth }).catch(() => {});
    const del = await request.delete(`/vms/${NAME}?confirm=true`, { headers: auth });
    // Without the NVRAM flag libvirt refuses to undefine a UEFI VM: the deletion itself is part of the check.
    expect([200, 204, 404]).toContain(del.status());
  }
  expect((await request.get(`/vms/${NAME}`, { headers: auth })).status()).toBe(404);
});
