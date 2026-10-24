import { expect, test, uiLogin, apiRoute } from "../support/fixtures";
import type { Route } from "@playwright/test";

// The HA page of a cluster (#333 step 7): automatic HA's banner, each member's state, admission control, the HA
// history, and an HA VM's settings changed in place. The test backend is no cluster: these endpoints are served by
// the test; what they answer is covered by tests/test_ha_page_api.py and the lab.
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());

const STATUS = {
  mode: "attempt", settings: { witness: "", suspect_threshold: 3, failure_threshold: 6 }, auto_restart_possible: true, reason: null, nodes: [],
  automatic: true, leading: true,
  admission: { policy: "warn", ok: false, unabsorbed: ["pve-b"], unknown: [], detail: "" },
  members: [
    { name: "pve-a", self: true, online: true, seen_s: 1, mem_free_mb: 6000, watchdog: { armed: true, reset_within_s: 125, stopped: false, at: "x" } },
    { name: "pve-b", self: false, online: false, seen_s: 90, mem_free_mb: 2000, watchdog: { armed: false, reset_within_s: null, stopped: true, at: "x" } },
  ],
};
const VM = { vm_name: "web", node: "pve-b", node_status: "offline", ha_state: "recovering", last_action: "restarting on pve-a", last_action_at: "2026-10-24T10:00:00+00:00", last_synced_at: "2026-10-24T09:59:00+00:00", priority: "high", preferred_nodes: [], monitoring: false };

test("the HA page of a cluster: automatic HA, members, admission, history, a VM's settings", async ({ page }) => {
  const sent: Array<[string, unknown]> = [];
  await uiLogin(page);
  await page.route(/\/ha(\/status|\/history.*|\/web\/(policy|monitoring)|\/admission)?(\?.*)?$/, (r) => {
    if (!api(r)) return r.fallback();
    const path = apiRoute(r.request().url()).split("?")[0];
    if (r.request().method() === "PUT") {
      sent.push([path, r.request().postDataJSON()]);
      return r.fulfill(json({ ...VM, ...r.request().postDataJSON() }));
    }
    if (path.endsWith("/ha/status")) return r.fulfill(json(STATUS));
    if (path.endsWith("/ha/history")) return r.fulfill(json({ items: [{ id: 1, timestamp: "2026-10-24T09:58:00+00:00", username: "system", action: "ha_lost", resource: "pve-b", result: "failed", error_message: "no status for more than 60 s" }] }));
    if (path.endsWith("/ha")) return r.fulfill(json({ items: [VM] }));
    return r.fallback();
  });

  await page.goto("/datacenter?tab=ha");
  const main = page.getByRole("main");
  await expect(main.getByText("Automatic HA: on.")).toBeVisible({ timeout: 20_000 });
  await expect(main.getByText("This node is the leader: it is the one acting.")).toBeVisible();
  await expect(main.getByText("Automatic HA: dry run.")).toHaveCount(0); // no dry run in a cluster

  const members = main.getByRole("region", { name: "Members" });
  await expect(members.getByText("Armed (reset within 125 s)")).toBeVisible();
  await expect(members.getByText("Silent for 90 s")).toBeVisible();
  await expect(members.getByText("Service stopped")).toBeVisible();

  const admission = main.getByRole("region", { name: "Admission control" });
  await expect(admission.getByText("If pve-b failed, the other members would not have the memory to restart all its HA VMs.")).toBeVisible();
  await admission.getByLabel("Policy").selectOption("strict");
  await expect.poll(() => sent.find(([p]) => p.endsWith("/ha/admission"))?.[1]).toEqual({ policy: "strict" });

  await expect(main.getByText("Restarting")).toBeVisible(); // the leader's state of the VM
  await expect(main.getByRole("region", { name: "HA history" }).getByText("Member lost")).toBeVisible();

  await main.getByRole("button", { name: "HA settings of web" }).click();
  const drawer = page.getByRole("dialog", { name: "HA settings of web" });
  await drawer.getByLabel("Restart priority").selectOption("low");
  await drawer.getByRole("switch", { name: "VM monitoring" }).click();
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect.poll(() => sent.find(([p]) => p.endsWith("/ha/web/policy"))?.[1]).toEqual({ priority: "low", preferred_nodes: [] });
  await expect.poll(() => sent.find(([p]) => p.endsWith("/ha/web/monitoring"))?.[1]).toEqual({ enabled: true });
});
