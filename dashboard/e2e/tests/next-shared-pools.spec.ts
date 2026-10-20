import { expect, goTo, test, uiLogin, apiRoute } from "../support/fixtures";
import type { Route } from "@playwright/test";

// Shared storage on several nodes, as on Proxmox. The test host is a single node: a remote node, the creation
// answer and the storage support are served here; the backend part is covered by tests/test_shared_pools.py.
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

test("an NFS pool goes on every node by default, each node's outcome is shown, a chosen set needs a node", async ({ page }) => {
  await page.route(/\/nodes(\/.*)?(\?.*)?$/, async (route) => {
    const req = route.request();
    if (req.resourceType() === "document") return route.fallback();
    const path = apiRoute(req.url());
    if (req.method() === "GET" && /\/nodes$/.test(path)) return json(route, [{ id: 9, name: "peer", hostname: "peer.example", ssh_user: "root", ssh_port: 22, status: "online" }]);
    if (req.method() === "GET" && /\/nodes\/peer\/summary$/.test(path)) return json(route, { connected: true, vms_running: 0, vms_stopped: 0 });
    return route.fallback();
  });
  await page.route("**/storage/support", (route) => json(route, { nfs: "ok", zfs: "ok", iscsi: "ok", iscsi_initiator: null }));
  const sent: Record<string, unknown>[] = [];
  await page.route(/\/storage(\?.*)?$/, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    sent.push(route.request().postDataJSON());
    return json(route, { name: "nas", shared: true, all_nodes: true, results: [
      { node: "local", state: "created", detail: null, warning: null },
      { node: "peer", state: "failed", detail: "mount.nfs: access denied by server", warning: null },
    ] }, 201);
  });
  await uiLogin(page);
  await goTo(page, "Storage");
  await page.getByRole("main").getByRole("button", { name: "Create a pool" }).click();
  const drawer = page.getByRole("dialog", { name: "Create a pool" });
  await drawer.getByRole("button", { name: "NFS share" }).click();
  const where = drawer.getByRole("group", { name: "Nodes" }).first();
  await expect(where.getByLabel("Every node (including those added later)")).toBeChecked();

  await where.getByLabel("Choose the nodes").check();
  await drawer.getByLabel("Pool name").fill("nas");
  await drawer.getByLabel("NFS server").fill("192.168.1.10");
  await drawer.getByLabel("Exported path").fill("/srv/vms");
  await drawer.getByRole("button", { name: "Create a pool" }).click();
  await expect(drawer.getByText("Choose at least one node.")).toBeVisible();
  expect(sent).toEqual([]);

  await where.getByLabel("Every node (including those added later)").check();
  await drawer.getByRole("button", { name: "Create a pool" }).click();
  await expect(page.getByText("Pool nas created on 1 node(s)")).toBeVisible();
  await expect(page.getByText("Failed on 1 node(s)")).toBeVisible();
  await expect(page.getByText(/access denied by server/).first()).toBeVisible();
  expect(sent[0]).toMatchObject({ name: "nas", type: "netfs", all_nodes: true });
});
