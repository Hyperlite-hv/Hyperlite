import { expect, test, uiLogin } from "../support/fixtures";
import type { Route } from "@playwright/test";

// The LDAP / Active Directory card of the authentication page: settings checked in the form, a test that names the
// role a user would get, saved without resending the stored password. The endpoints are served by the test; the
// sign-in itself is covered by tests/test_ldap_auth.py and was checked against a real OpenLDAP.
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const api = (r: Route) => ["fetch", "xhr"].includes(r.request().resourceType());

test("LDAP settings: check, test, save", async ({ page }) => {
  const sent: Array<[string, unknown]> = [];
  const saved = { enabled: false, url: "", starttls: false, verify_tls: true, ca_cert: "", bind_dn: "", base_dn: "", user_filter: "(&(objectClass=person)(|(uid={username})(sAMAccountName={username})))", group_attribute: "memberOf", admin_groups: "", allowed_groups: "", bind_password_set: false };
  await uiLogin(page);
  await page.route(/\/ldap\/(config|test)$/, (r) => {
    if (!api(r)) return r.fallback();
    const path = new URL(r.request().url()).pathname;
    if (r.request().method() === "GET") return r.fulfill(json(saved));
    sent.push([path, r.request().postDataJSON()]);
    if (path.endsWith("/test")) return r.fulfill(json({ connexion: true, utilisateur: { accepte: true, dn: "uid=alice,ou=people,dc=example,dc=org", groupes: ["cn=hv-admins"], role: "admin" } }));
    return r.fulfill(json({ ...saved, ...r.request().postDataJSON(), bind_password_set: true }));
  });

  await page.goto("/datacenter?tab=sso");
  const card = page.getByRole("region", { name: "LDAP / Active Directory" });
  await card.getByLabel("URL", { exact: true }).fill("http://dc1");
  await expect(card.getByText("ldap://host[:port] or ldaps://host[:port]")).toBeVisible();
  await card.getByLabel("URL", { exact: true }).fill("ldaps://dc1.example.org");
  await card.getByLabel("StartTLS (ldap:// upgraded to TLS)").check();
  await expect(card.getByText("StartTLS is for ldap:// URLs: ldaps:// is already encrypted")).toBeVisible();
  await card.getByLabel("StartTLS (ldap:// upgraded to TLS)").uncheck();
  await card.getByLabel("Search base", { exact: true }).fill("dc=example,dc=org");
  await card.getByLabel("Bind DN", { exact: true }).fill("cn=svc,dc=example,dc=org");
  await card.getByLabel("Password", { exact: true }).fill("svcpw");
  await card.getByLabel("Administrator groups", { exact: true }).fill("hv-admins");
  await card.getByLabel("User name (optional)").fill("alice");
  await card.getByLabel("Their password").fill("alicepw");
  await card.getByRole("button", { name: "Test with these settings" }).click();
  await expect(card.getByText("Accepted: uid=alice,ou=people,dc=example,dc=org, who would sign in as administrator.")).toBeVisible();
  await card.getByLabel("Sign in with directory accounts").check();
  await card.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Directory settings saved")).toBeVisible();
  await expect(card.getByText("Empty: keep the stored password.")).toBeVisible();

  expect(sent.map(([p]) => p)).toEqual(["/ldap/test", "/ldap/config"]);
  expect(sent[1][1]).toMatchObject({ enabled: true, url: "ldaps://dc1.example.org", base_dn: "dc=example,dc=org", bind_password: "svcpw", admin_groups: "hv-admins" });
});
