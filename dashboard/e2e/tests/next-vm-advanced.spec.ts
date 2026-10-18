import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// Advanced hardware settings under a VM's Hardware tab. The VM is served by the test; the libvirt side is covered by
// tests/test_vm_hardware_opts.py.
const NAME = "e2e-adv-web";
const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const vm = { name: NAME, state: "stopped", id: null, uuid: NAME, vcpu: 1, memory_mb: 2048, ip: null, ssh_user: null, uptime_s: null, os: "Debian", zfs_storage: false, iscsi_storage: false, guest_agent: null, firmware: "bios" };

test("advanced hardware: disk options, boot order, ballooning and machine type", async ({ page }) => {
  let state = {
    disks: [
      { target: "vda", type: "disk", bus: "virtio", cache: null, discard: null, io: null, iothread: false, iops: null, mbps: null },
      { target: "sdz", type: "cdrom", bus: "sata", cache: null, discard: null, io: null, iothread: false, iops: null, mbps: null },
    ],
    interfaces: ["net:52:54:00:00:00:01"],
    boot_order: ["vda", "sdz"],
    ballooning: { active: true, memory_mb: 2048, minimum_mb: 2048 },
    machine: "pc-q35-6.2", newer_machines: ["pc-q35-9.0"], running: false,
  };
  const calls: Array<[string, unknown]> = [];
  await uiLogin(page);
  await page.route(/\/vms(\?.*)?$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([vm])) : r.fallback()));
  await page.route(new RegExp(`/vms/${NAME}/(hardware-options|disks/vda/options|boot-order|balloon|machine)$`), (r) => {
    if (!api(r)) return r.fallback();
    const path = new URL(r.request().url()).pathname.split(`/vms/${NAME}/`)[1];
    if (r.request().method() === "PUT") {
      const body = r.request().postDataJSON();
      calls.push([path, body]);
      if (path === "boot-order") state = { ...state, boot_order: body.position };
      if (path === "balloon") state = { ...state, ballooning: { ...state.ballooning, active: body.active, minimum_mb: body.minimum_mb ?? 2048 } };
      if (path === "machine") state = { ...state, machine: body.machine, newer_machines: [] };
      if (path === "disks/vda/options") state = { ...state, disks: [{ ...state.disks[0], ...body }, state.disks[1]] };
      return r.fulfill(json({ ...state, needs_restart: path === "disks/vda/options" }));
    }
    return r.fulfill(json(state));
  });

  await page.goto(`/vm/${NAME}?tab=advanced`);
  const main = page.getByRole("main");
  await main.getByLabel("Cache vda").selectOption("writeback");
  await main.getByLabel("I/O mode vda").selectOption("native");
  await expect(main.getByText("native needs cache none or directsync")).toBeVisible();
  await main.getByLabel("Cache vda").selectOption("none");
  await main.getByLabel("I/O thread vda").check();
  await main.getByLabel("IOPS vda").fill("500");
  await main.getByRole("row", { name: /vda/ }).getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Applies at the VM's next start.")).toBeVisible();

  const boot = main.getByRole("region", { name: "Boot order" });
  await boot.getByRole("button", { name: /^Network 52:54:00:00:00:01$/ }).click();
  await boot.getByRole("button", { name: "Move net:52:54:00:00:00:01 up" }).click();
  await boot.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Boot order saved")).toBeVisible();

  const balloon = main.getByRole("region", { name: "Memory ballooning" });
  await balloon.getByLabel("Minimum memory", { exact: true }).fill("100");
  await expect(balloon.getByText("From 256 to 2048 MB (the VM's memory).")).toBeVisible();
  await balloon.getByLabel("Minimum memory", { exact: true }).fill("1024");
  await balloon.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Ballooning saved")).toBeVisible();

  await main.getByRole("button", { name: "Update to pc-q35-9.0" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Update" }).click();
  await expect(page.getByText("Machine type updated")).toBeVisible();

  expect(calls).toEqual([
    ["disks/vda/options", { cache: "none", discard: null, io: "native", iothread: true, iops: 500, mbps: null }],
    ["boot-order", { position: ["vda", "net:52:54:00:00:00:01", "sdz"] }],
    ["balloon", { active: true, minimum_mb: 1024 }],
    ["machine", { machine: "pc-q35-9.0" }],
  ]);
});
