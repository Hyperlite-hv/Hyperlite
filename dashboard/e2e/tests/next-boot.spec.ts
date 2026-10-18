import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// Start at boot in a VM's Options tab. The VM is served by the test (no hypervisor needed); the backend part is
// covered by tests/test_vm_boot.py.
const NAME = "e2e-boot-db";
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const vm = { name: NAME, state: "stopped", id: null, uuid: NAME, vcpu: 1, memory_mb: 512, ip: null, ssh_user: null, uptime_s: null, os: "Debian", zfs_storage: false, iscsi_storage: false, agent_invite: null, firmware: "bios" };
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());

test("start at boot: enabled with an order and a pause, validated, saved, libvirt's own flag explained", async ({ page }) => {
  let boot = { autostart: false, position: null as number | null, delay_s: 0, autostart_libvirt: true };
  const saved: unknown[] = [];
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/limits$`), (r) => (api(r) ? r.fulfill(json({ cpu_shares: 1024, cpu_limit_pct: null, mem_hard_limit_mb: null })) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/cpu-pinning$`), (r) => (api(r) ? r.fulfill(json({ cpus: null, strict: false, numa_cell: null, topology: { cpus: [{ id: 0, cell: 0 }, { id: 1, cell: 0 }], cells: [0] } })) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/boot(\\?.*)?$`), (r) => {
    if (!api(r)) return r.fallback();
    if (r.request().method() === "PUT") {
      const body = r.request().postDataJSON();
      saved.push(body);
      boot = { ...body, autostart_libvirt: false };
    }
    return r.fulfill(json(boot));
  });

  await page.goto(`/vm/${NAME}?tab=options`);
  const card = page.getByRole("main").getByRole("region", { name: "Start at boot" });
  await expect(card).toBeVisible();
  await expect(card.getByText(/libvirt's own autostart is set on this VM/)).toBeVisible();
  const save = card.getByRole("button", { name: "Save" });
  await expect(save).toBeDisabled();
  await expect(card.getByLabel("Order")).toBeDisabled(); // only meaningful once start at boot is on

  await card.getByLabel("Start this VM when its node starts").check();
  await card.getByLabel("Order").fill("-3");
  await expect(card.getByText("A whole number from 0 to 9999, or empty.")).toBeVisible();
  await expect(save).toBeDisabled();
  await card.getByLabel("Order").fill("10");
  await card.getByLabel("Pause before the next VM").fill("30");
  await save.click();

  await expect(page.getByText("Start at boot saved")).toBeVisible();
  expect(saved).toEqual([{ autostart: true, position: 10, delay_s: 30 }]);
  await expect(card.getByText(/libvirt's own autostart is set/)).toHaveCount(0);
  await expect(save).toBeDisabled(); // saved: nothing left to save
});
