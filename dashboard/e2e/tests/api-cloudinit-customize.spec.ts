import { execFileSync } from "node:child_process";
import { apiLogin, expect, PREFIX, test } from "../support/fixtures";

// A guest's customisation, checked inside a real guest: a VM made from the cloud image with a static address on the
// isolated network answers at that address once booted (cloud-init applied the drive's network configuration).
const stamp = Date.now().toString().slice(-6);
const VM = `${PREFIX}cust-${stamp}`;
const ADDRESS = `192.168.199.${200 + (Number(stamp) % 50)}`;
let token = "";
const auth = () => ({ Authorization: `Bearer ${token}` });

test.describe.configure({ mode: "serial", timeout: 360_000 });

test.beforeAll(async ({ request }) => {
  token = await apiLogin(request);
});

test.afterAll(async ({ request }) => {
  if ((await request.get(`/vms/${VM}`, { headers: auth() })).ok()) {
    await request.post(`/vms/${VM}/stop?force=true`, { headers: auth() });
    await request.delete(`/vms/${VM}?confirm=true`, { headers: auth() });
  }
});

function answers(address: string) {
  try {
    execFileSync("ping", ["-c", "1", "-W", "2", address], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

test("a VM created with a static address answers there once booted", async ({ request }) => {
  expect(answers(ADDRESS)).toBe(false);
  const created = await request.post("/vms", {
    headers: auth(),
    data: {
      name: VM, vcpu: 1, memory_mb: 512, disks: [{ size_gb: 3 }], network: "hyperlite-isolated", username: "tester", password: "Testpass1",
      customize: { hostname: "e2e-custom", domain: "example.internal", network: [{ addresses: [`${ADDRESS}/24`], gateway: "192.168.199.1" }], dns: ["192.168.199.1"] },
    },
  });
  expect(created.status(), await created.text()).toBe(201);

  const ci = (await (await request.get(`/vms/${VM}/cloud-init`, { headers: auth() })).json()) as { hostname: string; domain: string; network: Array<{ addresses: string[]; gateway: string }>; dns: string[] };
  expect(ci.hostname).toBe("e2e-custom");
  expect(ci.domain).toBe("example.internal");
  expect(ci.network[0].addresses).toEqual([`${ADDRESS}/24`]);
  expect(ci.dns).toEqual(["192.168.199.1"]);

  const started = await request.post(`/vms/${VM}/start`, { headers: auth() });
  expect(started.ok(), await started.text()).toBe(true);
  await expect.poll(() => answers(ADDRESS), { timeout: 300_000, intervals: [5_000] }).toBe(true);
});
