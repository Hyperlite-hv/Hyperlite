import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// The node's HTTPS certificate on its System page: shown, a PEM pair imported (after a confirmation that the service
// restarts), a Let's Encrypt request refused with certbot's reason. The endpoints are served by the test; the
// checks on the pair are covered by tests/test_tls_certs.py.
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());
const selfSigned = {
  present: true, source: "auto", certbot: true, precedent: false, sujet: "hv1", emetteur: "CN=hv1", noms: ["hv1", "localhost"],
  debut: "2026-01-01T00:00:00+00:00", fin: "2036-01-01T00:00:00+00:00", jours_restants: 3380, auto_signe: true, empreinte_sha256: "AA:BB",
};

test("certificate: import a PEM pair, and a failed Let's Encrypt request shows certbot's reason", async ({ page }) => {
  let cert = selfSigned;
  const posted: Array<[string, unknown]> = [];
  await uiLogin(page);
  await page.route(/\/host\/certificate(\/[a-z-]+)?$/, (r) => {
    if (!api(r)) return r.fallback();
    if (r.request().method() === "GET") return r.fulfill(json(cert));
    const path = new URL(r.request().url()).pathname;
    posted.push([path, r.request().postDataJSON()]);
    if (path.endsWith("/acme")) return r.fulfill(json({ detail: "certbot failed:\nTimeout during connect (likely firewall problem)" }, 502));
    cert = { ...selfSigned, source: "import", auto_signe: false, precedent: true, sujet: "hv1.example.org", emetteur: "CN=Example CA", noms: ["hv1.example.org"], jours_restants: 12 };
    return r.fulfill(json({ ...cert, redemarrage: true }));
  });

  await page.goto("/node/local?tab=system");
  const card = page.getByRole("region", { name: "HTTPS certificate" });
  await expect(card.getByText("self-signed")).toBeVisible();
  await expect(card.getByText("Generated at installation")).toBeVisible();

  await card.getByRole("button", { name: "Import" }).click();
  const form = card.getByRole("group", { name: "Import" });
  await expect(form.getByRole("button", { name: "Install the certificate" })).toBeDisabled();
  await form.getByLabel("Certificate (PEM)").fill("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----");
  await form.getByLabel("Private key (PEM)").fill("-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----");
  await form.getByRole("button", { name: "Install the certificate" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Install and restart" }).click();
  await expect(page.getByText("Hyperlite is restarting to use it.")).toBeVisible();
  await expect(card.getByText("CN=Example CA")).toBeVisible();
  await expect(card.getByText("12 days left")).toBeVisible();
  await expect(card.getByRole("button", { name: "Go back to the previous certificate" })).toBeVisible();

  await card.getByRole("button", { name: "Let's Encrypt" }).click();
  const acme = card.getByRole("group", { name: "Let's Encrypt" });
  await acme.getByLabel("Domain name").fill("hv1");
  await expect(acme.getByText("A public DNS name such as hv1.example.org")).toBeVisible();
  await acme.getByLabel("Domain name").fill("hv1.example.org");
  await acme.getByLabel("E-mail").fill("ops@example.org");
  await acme.getByRole("button", { name: "Request the certificate" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Install and restart" }).click();
  await expect(page.getByText(/likely firewall problem/)).toBeVisible();

  expect(posted).toEqual([
    ["/host/certificate", { certificat: "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----", cle: "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----", chaine: "" }],
    ["/host/certificate/acme", { domaine: "hv1.example.org", email: "ops@example.org", test: false }],
  ]);
});
