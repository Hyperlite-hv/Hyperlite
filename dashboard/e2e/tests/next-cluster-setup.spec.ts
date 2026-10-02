import { expect, goTo, test, uiLogin } from "../support/fixtures";

// Administration › Replicated configuration, the Cluster card (app/core/cluster_setup.py, covered by
// tests/test_cluster_setup.py): the test backend is a node in no cluster, and the tests play the routes of /cluster for
// the rest.
test("a node in no cluster shows the card; without Corosync it says how to install it", async ({ page }) => {
  await uiLogin(page);
  await goTo(page, "Replicated configuration");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Cluster", exact: true })).toBeVisible();  // the real answer

  await page.route(/\/cluster$/, (route) => route.fulfill({ json: { corosync_installe: false, en_cluster: false, nom: null, membres: [], noeud: "pve-a" } }));
  await page.reload();
  await expect(main.getByRole("note").filter({ hasText: "apt install corosync" })).toBeVisible();
  await expect(main.getByRole("button", { name: "Create the cluster" })).toBeDisabled();
  await expect(main.getByRole("button", { name: "Join the cluster" })).toBeDisabled();
});

const ALONE = { corosync_installe: true, en_cluster: false, nom: null, membres: [], noeud: "pve-a" };
const IN_CLUSTER = {
  corosync_installe: true, en_cluster: true, nom: "prod", quorum: true, noeud: "pve-a",
  membres: [{ nom: "pve-a", nodeid: 1, adresse: "192.0.2.10" }, { nom: "pve-b", nodeid: 2, adresse: "192.0.2.11" }],
};

test("creating a cluster, then copying the join information and removing a member after a confirmation", async ({ page }) => {
  let state: object = ALONE;
  const bodies: Record<string, unknown>[] = [];
  await page.route(/\/cluster$/, (route) => route.fulfill({ json: state }));
  await page.route(/\/cluster\/creer$/, (route) => { bodies.push(route.request().postDataJSON()); state = IN_CLUSTER; return route.fulfill({ json: state }); });
  await page.route(/\/cluster\/adhesion$/, (route) => route.fulfill({ json: { information: "eyJjbHVzdGVyIjoicHJvZCJ9", cluster: "prod", expire: "2026-10-02T13:00:00+00:00" } }));
  await page.route(/\/cluster\/membres\/pve-b\/retirer$/, (route) => {
    bodies.push(route.request().postDataJSON());
    state = { ...IN_CLUSTER, membres: IN_CLUSTER.membres.slice(0, 1) };
    return route.fulfill({ json: state });
  });
  await uiLogin(page);
  await goTo(page, "Replicated configuration");
  const main = page.getByRole("main");

  const create = main.getByRole("form", { name: "Create a cluster" });
  await create.getByLabel("Cluster name").fill("prod");
  await create.getByLabel("Address of this node on the cluster network").fill("192.0.2.10");
  await create.getByRole("button", { name: "Create the cluster" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Create the cluster" }).click();
  await expect(main.getByRole("heading", { name: "Cluster prod" })).toBeVisible();
  expect(bodies[0]).toEqual({ nom: "prod", adresse: "192.0.2.10" });
  const members = main.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "Corosync id" }) });
  await expect(members.getByRole("row", { name: /pve-a.*this node/ })).toBeVisible();
  await expect(members.getByRole("button", { name: "Remove pve-a from the cluster" })).toHaveCount(0);

  await main.getByRole("button", { name: "Join information" }).click();
  await expect(main.getByLabel("Join information")).toHaveValue("eyJjbHVzdGVyIjoicHJvZCJ9");

  await members.getByRole("button", { name: "Remove pve-b from the cluster" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  expect(bodies).toHaveLength(1);
  await members.getByRole("button", { name: "Remove pve-b from the cluster" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove" }).click();
  await expect(members.getByRole("row", { name: /pve-b/ })).toHaveCount(0);
  expect(bodies[1]).toEqual({ confirmation: "pve-b" });
});

test("joining needs the cluster's name typed back, in English and in French", async ({ page }) => {
  const sent: Record<string, unknown>[] = [];
  await page.route(/\/cluster$/, (route) => route.fulfill({ json: ALONE }));
  await page.route(/\/cluster\/rejoindre$/, (route) => { sent.push(route.request().postDataJSON()); return route.fulfill({ json: { cluster: "prod", redemarrage: true } }); });
  await uiLogin(page);
  await goTo(page, "Replicated configuration");
  const main = page.getByRole("main");
  const join = main.getByRole("form", { name: "Join a cluster" });
  // base64url of {"cluster":"prod","adresse":"192.0.2.10"}
  const information = btoa(JSON.stringify({ cluster: "prod", adresse: "192.0.2.10" })).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  await join.getByLabel("Join information").fill(information);
  await join.getByLabel("Address of this node on the cluster network").fill("192.0.2.11");
  await expect(join.getByText("Type the cluster's name, prod, to confirm")).toBeVisible();
  const go = join.getByRole("button", { name: "Join the cluster" });
  await join.getByLabel(/Type the cluster's name/).fill("pro");
  await expect(go).toBeDisabled();
  await join.getByLabel(/Type the cluster's name/).fill("prod");
  await go.click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Join the cluster" }).click();
  await expect(page.getByText("This node joined prod")).toBeVisible();
  expect(sent).toEqual([{ information, adresse: "192.0.2.11", confirmation: "prod" }]);

  await page.evaluate(() => localStorage.setItem("hyperlite-next-lang", "fr"));
  await page.reload();
  await expect(main.getByRole("form", { name: "Rejoindre un cluster" })).toBeVisible({ timeout: 20_000 });
  await expect(main.getByRole("button", { name: "Créer le cluster" })).toBeVisible();
});
