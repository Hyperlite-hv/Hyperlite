import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// Editing a NAT network (DHCP range live, subnet at the next restart) and its reservations, one made from a lease.
// The network is served by the test; the libvirt side is covered by tests/test_network_edit.py.
const NAME = "e2e-ipam-lab";
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());

test("network: edit subnet and DHCP, restart, reserve an address from a lease and remove it", async ({ page }) => {
  let ipam = { modifiable: true, mode: "nat", adresse: "10.9.0.1", masque: "255.255.255.0", dhcp: { debut: "10.9.0.100", fin: "10.9.0.200" }, reservations: [] as Array<{ mac: string; ip: string; nom: string | null }> };
  let pending = false;
  const calls: Array<[string, string, unknown]> = [];
  const summary = () => ({ nom: NAME, uuid: "u", actif: true, autostart: true, pont: "virbr9", macvtap: false, type: ipam.mode, reseau: { adresse: ipam.adresse, masque: ipam.masque }, dhcp: Boolean(ipam.dhcp), vms: 2 });
  await uiLogin(page);
  await page.route(/\/networks$/, (r) => (api(r) && r.request().method() === "GET" ? r.fulfill(json([summary()])) : r.fallback()));
  await page.route(new RegExp(`/networks/${NAME}(/.*)?(\\?.*)?$`), (r) => {
    if (!api(r)) return r.fallback();
    const url = new URL(r.request().url());
    const rest = url.pathname.split(`/networks/${NAME}`)[1];
    const method = r.request().method();
    if (rest === "/firewall") return r.fulfill(json({ default_policy: "accept", rules: [] }));
    if (method !== "GET") calls.push([method, rest, r.request().postDataJSON?.() ?? null]);
    if (method === "PATCH") {
      const body = r.request().postDataJSON();
      pending = body.masque !== ipam.masque;
      ipam = { ...ipam, mode: body.mode, adresse: body.adresse, masque: body.masque, dhcp: body.dhcp };
      return r.fulfill(json({ ...summary(), ipam, a_redemarrer: pending }));
    }
    if (method === "POST" && rest === "/reservations") {
      const b = r.request().postDataJSON();
      ipam = { ...ipam, reservations: [...ipam.reservations, { mac: b.mac, ip: b.ip, nom: b.nom }] };
      return r.fulfill(json(ipam, 201));
    }
    if (method === "DELETE" && rest.startsWith("/reservations/")) {
      ipam = { ...ipam, reservations: [] };
      return r.fulfill(json(ipam));
    }
    if (rest === "/stop" || rest === "/start") { if (rest === "/start") pending = false; return r.fulfill(json(summary())); }
    return r.fulfill(json({ ...summary(), ipam, a_redemarrer: pending, baux_dhcp: [{ mac: "52:54:00:00:00:09", ip: "10.9.0.150", hostname: "db", expire: 1900000000 }] }));
  });

  await page.goto("/datacenter?tab=reseau");
  await page.getByRole("button", { name: `Details of ${NAME}` }).click();
  const settings = page.getByRole("region", { name: "Subnet and DHCP" });
  await settings.getByLabel("DHCP start").fill("10.9.0.300");
  await expect(settings.getByRole("button", { name: "Save" })).toBeDisabled();
  await settings.getByLabel("DHCP start").fill("10.9.0.50");
  await settings.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Applied.")).toBeVisible();

  await settings.getByLabel("Netmask", { exact: true }).fill("255.255.0.0");
  await settings.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Applies at the network's next restart.")).toBeVisible();
  await expect(settings.getByText("Saved changes wait for a restart of the network.")).toBeVisible();
  await settings.getByRole("button", { name: "Restart the network" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Restart the network" }).click();
  await expect(page.getByText("Network restarted")).toBeVisible();
  await expect(settings.getByText("Saved changes wait for a restart of the network.")).toHaveCount(0);

  const addresses = page.getByRole("region", { name: "Addresses" });
  await addresses.getByRole("button", { name: "Reserve 10.9.0.150" }).click();
  const add = addresses.getByRole("group", { name: "Add a reservation" });
  await expect(add.getByLabel("MAC address", { exact: true })).toHaveValue("52:54:00:00:00:09");
  await expect(add.getByLabel("Name (optional)")).toHaveValue("db");
  await add.getByRole("button", { name: "Add a reservation" }).click();
  await expect(addresses.getByRole("button", { name: "Reserve 10.9.0.150" })).toHaveCount(0);
  await addresses.getByRole("button", { name: "Remove the reservation of 10.9.0.150" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove" }).click();
  await expect(addresses.getByText("No reservation.")).toBeVisible();

  expect(calls).toEqual([
    ["PATCH", "", { mode: "nat", adresse: "10.9.0.1", masque: "255.255.255.0", dhcp: { debut: "10.9.0.50", fin: "10.9.0.200" } }],
    ["PATCH", "", { mode: "nat", adresse: "10.9.0.1", masque: "255.255.0.0", dhcp: { debut: "10.9.0.50", fin: "10.9.0.200" } }],
    ["POST", "/stop", null],
    ["POST", "/start", null],
    ["POST", "/reservations", { mac: "52:54:00:00:00:09", ip: "10.9.0.150", nom: "db" }],
    ["DELETE", "/reservations/52%3A54%3A00%3A00%3A00%3A09", null],
  ]);
});
