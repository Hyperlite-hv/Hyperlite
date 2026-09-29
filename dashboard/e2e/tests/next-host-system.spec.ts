import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// The node's system settings: package updates (security filter, Hyperlite's own package left out, the upgrade
// started as a task) and DNS, time and remote syslog on its System page. The endpoints are served by the test; the
// commands and files are covered by tests/test_host_system.py.
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());

test("node updates and system settings", async ({ page }) => {
  const sent: Array<[string, string, unknown]> = [];
  await uiLogin(page);
  await page.route(/\/host\/system\/.*/, (r) => {
    if (!api(r)) return r.fallback();
    const path = new URL(r.request().url()).pathname.replace("/host/system/", "");
    const method = r.request().method();
    if (method !== "GET") sent.push([method, path, r.request().postDataJSON()]);
    if (path === "updates") return r.fulfill(json({
      disponible: true, redemarrage_requis: true, paquets_redemarrage: ["linux-image-6.1"], actualise_le: 1900000000,
      paquets: [
        { nom: "openssl", version: "3.0.13-2", depuis: "3.0.13-1", securite: true },
        { nom: "base-files", version: "13.5", depuis: "13.4", securite: false },
        { nom: "hyperlite", version: "2026.10.1", depuis: "2026.09.1", securite: false },
      ],
    }));
    if (path === "updates/upgrade") return r.fulfill(json({ tache: "t1" }, 202));
    if (path === "dns") return r.fulfill(json({ gere_par: "fichier", modifiable: true, serveurs: method === "PUT" ? ["9.9.9.9"] : ["1.1.1.1"], recherche: [] }));
    if (path === "time/zones") return r.fulfill(json(["Etc/UTC", "Europe/Paris"]));
    if (path === "time") return r.fulfill(json({ fuseau: method === "PUT" ? "Europe/Paris" : "Etc/UTC", ntp: true, synchronise: true, timesyncd: true, serveurs_ntp: [] }));
    if (path === "syslog") return r.fulfill(json({ disponible: true, cible: method === "PUT" ? { hote: "logs.example.org", port: 514, protocole: "udp" } : null }));
    return r.fallback();
  });
  await page.route(/\/host\/certificate$/, (r) => (api(r) ? r.fulfill(json({ present: false, source: "auto", certbot: false })) : r.fallback()));

  await page.goto("/node/local?tab=updates");
  const main = page.getByRole("main");
  await expect(main.getByText("This node needs a reboot to finish an update (linux-image-6.1)")).toBeVisible();
  await expect(main.getByText("Hyperlite 2026.10.1 is available")).toBeVisible();
  const table = main.getByRole("region", { name: "Package updates" });
  await expect(table.getByRole("row")).toHaveCount(3);
  await main.getByLabel("Security only (1)").check();
  await expect(table.getByRole("row")).toHaveCount(2);
  await main.getByRole("button", { name: "Upgrade (1)" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Upgrade" }).click();
  await expect(page).toHaveURL(/tab=tasks/);

  await page.goto("/node/local?tab=system");
  const dns = main.getByRole("region", { name: "DNS" });
  await dns.getByLabel("DNS servers").fill("9.9.9.9 dns.example");
  await expect(dns.getByText("1 to 3 IP addresses")).toBeVisible();
  await dns.getByLabel("DNS servers").fill("9.9.9.9");
  await dns.getByRole("button", { name: "Save" }).click();
  await expect(dns.getByLabel("DNS servers")).toHaveValue("9.9.9.9");

  const time = main.getByRole("region", { name: "Time" });
  await time.getByLabel("Time zone").selectOption("Europe/Paris");
  await time.getByRole("button", { name: "Save" }).click();
  await expect(time.getByLabel("Time zone")).toHaveValue("Europe/Paris");

  const syslog = main.getByRole("region", { name: "Remote syslog" });
  await syslog.getByLabel("Server").fill("logs.example.org");
  await syslog.getByRole("button", { name: "Save" }).click();
  await expect(syslog.getByText("forwarding", { exact: true })).toBeVisible();

  expect(sent).toEqual([
    ["POST", "updates/upgrade", { paquets: ["openssl"] }],
    ["PUT", "dns", { serveurs: ["9.9.9.9"], recherche: [] }],
    ["PUT", "time", { fuseau: "Europe/Paris", ntp: null, serveurs_ntp: [] }],
    ["PUT", "syslog", { hote: "logs.example.org", port: 514, protocole: "udp" }],
  ]);
});
