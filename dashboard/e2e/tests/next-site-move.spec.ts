import { expect, goTo, listing, test, uiLogin } from "../support/fixtures";

// A VM moved to another cluster from its actions (docs/design/multi-cluster.md, section 9): the remote, its node, a
// pool per disk and a network per card, what stays behind said, and the request the move sends. The VM, the remote and
// its lists are given by the test (no second cluster in the e2e backend); nothing is moved.
const FAKE = { name: "e2e-move-demo", state: "stopped", vcpu: 1, memory_mb: 512, ip: null, uuid: "00000000-0000-0000-0000-00000000a0e1", os: null, uptime_s: null };
const SPEC = {
  name: FAKE.name, uuid: FAKE.uuid, disks: [{ dev: "sda", size: 10 * 1024 ** 3 }],
  nics: [{ mac: "52:54:00:aa:bb:01", model: "virtio", network: "lan" }],
};

test("a stopped VM is moved to another cluster from its actions", async ({ page }) => {
  // The list is the test's whole: a real answer read and completed here was disposed when the page asked again
  // meanwhile (seen in CI), and this VM is all the test needs.
  await page.route(/\/vms(\?cluster=true)?$/, (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: listing([{ ...FAKE, node: "local" }]) }) : route.continue());
  await page.route(/\/api\/v1\/sites\/outgoing\/e2e-move-demo$/, async (route) => {
    if (route.request().method() === "POST") return route.fulfill({ status: 202, json: { task_id: "t-move", status: "running" } });
    return route.fulfill({ json: { spec: SPEC, left: ["CPU pinning (it names this host's CPUs)"], allocated: { sda: 3 * 1024 ** 3 } } });
  });
  await page.route(/\/api\/v1\/sites$/, (route) => route.fulfill({ json: listing([
    { name: "here", self: true, reachable: true, summary: { nodes: [] } },
    { name: "lyon", self: false, reachable: true, summary: { nodes: [{ name: "ly1", online: true }, { name: "ly2", online: false }] } },
  ]) }));
  await page.route(/\/api\/v1\/sites\/remotes$/, (route) => route.fulfill({ json: listing([{ name: "lyon", uses: ["manage", "view"], endpoints: [] }]) }));
  await page.route(/\/api\/v1\/sites\/lyon\/storage\?node=ly1$/, (route) => route.fulfill({ json: listing([
    { name: "default", type: "dir", state: "active", available_gb: 120 }, { name: "san", type: "iscsi", state: "active", available_gb: 900 },
  ]) }));
  await page.route(/\/api\/v1\/sites\/lyon\/networks\?node=ly1$/, (route) => route.fulfill({ json: listing([{ name: "default" }, { name: "lan" }]) }));

  await uiLogin(page);
  await goTo(page, "Virtual Machines");
  await page.getByRole("row", { name: /e2e-move-demo/ }).click({ button: "right", position: { x: 40, y: 10 } });
  await page.getByRole("menu", { name: "Actions of e2e-move-demo" }).getByRole("menuitem", { name: /Move to another cluster/ }).click();
  const drawer = page.getByRole("dialog", { name: "Move e2e-move-demo to another cluster" });
  await expect(drawer.getByText("CPU pinning (it names this host's CPUs)")).toBeVisible();
  await drawer.getByLabel("Cluster").selectOption("lyon");
  await expect(drawer.getByLabel("Node").locator("option")).toHaveText(["Choose…", "ly1"]); // the node that does not answer is not offered
  await drawer.getByLabel("Node").selectOption("ly1");
  await expect(drawer.getByLabel(/Pool for sda/).locator("option")).toHaveText([/default · 120 GiB/]); // an iSCSI pool cannot receive one
  await expect(drawer.getByLabel(/Network for 52:54:00:aa:bb:01/)).toHaveValue("lan"); // the same name, chosen first
  await drawer.getByLabel("Bandwidth limit (MiB/s)").fill("50");
  const sent = page.waitForRequest((r) => r.method() === "POST" && /\/sites\/outgoing\/e2e-move-demo$/.test(r.url()));
  await drawer.getByRole("button", { name: "Move", exact: true }).click();
  expect((await sent).postDataJSON()).toEqual({ remote: "lyon", node: "ly1", pools: ["default"], networks: ["lan"], name: null, start: false, limit_mib: 50 });
  await expect(page.getByText("Move started")).toBeVisible();
});

test("a running VM is moved live, keeping its name", async ({ page }) => {
  const RUNNING = { ...FAKE, name: "e2e-live-demo", state: "active", uuid: "00000000-0000-0000-0000-00000000a0e2" };
  await page.route(/\/vms(\?cluster=true)?$/, (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: listing([{ ...RUNNING, node: "local" }]) }) : route.continue());
  let asked = "";
  await page.route(/\/api\/v1\/sites\/outgoing\/e2e-live-demo(\?live=true)?$/, async (route) => {
    if (route.request().method() === "POST") return route.fulfill({ status: 202, json: { task_id: "t-live", status: "running" } });
    asked = new URL(route.request().url()).search;
    return route.fulfill({ json: { spec: { ...SPEC, name: RUNNING.name }, left: [], allocated: { sda: 1024 ** 3 }, cpu: "<cpu mode='custom'/>" } });
  });
  await page.route(/\/api\/v1\/sites$/, (route) => route.fulfill({ json: listing([
    { name: "lyon", self: false, reachable: true, summary: { nodes: [{ name: "ly1", online: true }] } },
  ]) }));
  await page.route(/\/api\/v1\/sites\/remotes$/, (route) => route.fulfill({ json: listing([{ name: "lyon", uses: ["manage", "view"], endpoints: [] }]) }));
  await page.route(/\/api\/v1\/sites\/lyon\/storage\?node=ly1$/, (route) => route.fulfill({ json: listing([{ name: "default", type: "dir", state: "active", available_gb: 120 }]) }));
  await page.route(/\/api\/v1\/sites\/lyon\/networks\?node=ly1$/, (route) => route.fulfill({ json: listing([{ name: "lan" }]) }));

  await uiLogin(page);
  await goTo(page, "Virtual Machines");
  await page.getByRole("row", { name: /e2e-live-demo/ }).click({ button: "right", position: { x: 40, y: 10 } });
  await page.getByRole("menu", { name: "Actions of e2e-live-demo" }).getByRole("menuitem", { name: /Move to another cluster/ }).click();
  const drawer = page.getByRole("dialog", { name: "Move e2e-live-demo live to another cluster" });
  await expect(drawer.getByText(/keeps running/).first()).toBeVisible();
  expect(asked).toBe("?live=true");
  await drawer.getByLabel("Cluster").selectOption("lyon");
  await drawer.getByLabel("Node").selectOption("ly1");
  await expect(drawer.getByLabel(/Pool for sda/)).toHaveValue("default");
  await expect(drawer.getByLabel("Name there")).toHaveCount(0); // it keeps its name
  const sent = page.waitForRequest((r) => r.method() === "POST" && /\/sites\/outgoing\/e2e-live-demo$/.test(r.url()));
  await drawer.getByRole("button", { name: "Move", exact: true }).click();
  expect((await sent).postDataJSON()).toEqual({ remote: "lyon", node: "ly1", pools: ["default"], networks: ["lan"], limit_mib: null, live: true });
});
