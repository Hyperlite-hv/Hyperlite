import { useEffect, useState } from "react";
import { fetchLdapConfig, saveLdapConfig, testLdap } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "./States";
import { Card, Field, Loading } from "./ui";

const EMPTY = { enabled: false, url: "", starttls: false, verify_tls: true, ca_cert: "", bind_dn: "", bind_password: "", base_dn: "",
  user_filter: "(&(objectClass=person)(|(uid={username})(sAMAccountName={username})))", group_attribute: "memberOf", admin_groups: "", allowed_groups: "" };
const URL_RE = /^ldaps?:\/\/[A-Za-z0-9.[\]:-]+(:\d{1,5})?\/?$/;

// LDAP / Active Directory sign-in, next to SSO: the same sign-in form, checked against the directory for names
// that are not local accounts. The test button binds with the service account and, with a user's name and
// password, says which role they would get, without creating the account.
export default function LdapCard() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [saved, setSaved] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [probe, setProbe] = useState({ username: "", password: "" });
  const [result, setResult] = useState(null);
  const fill = (c) => { setSaved(c); setForm({ ...EMPTY, ...c, bind_password: "" }); };
  useEffect(() => { fetchLdapConfig().then(fill).catch((e) => setError(errorMessage(e))); }, []);
  if (error && !saved) return <Card title={t("ldap.title")}><ErrorState message={error} /></Card>;
  if (!saved) return <Card title={t("ldap.title")}><Loading /></Card>;

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const bad = {
    url: form.url !== "" && !URL_RE.test(form.url.trim()),
    base_dn: form.enabled && !form.base_dn.trim(),
    user_filter: !form.user_filter.includes("{username}"),
    starttls: form.starttls && form.url.startsWith("ldaps://"),
  };
  const invalid = Object.values(bad).some(Boolean) || (form.enabled && !form.url);
  const payload = () => ({ ...form, url: form.url.trim(), bind_password: form.bind_password || null });

  async function save() {
    setBusy(true);
    try { fill(await saveLdapConfig(payload())); pushToast({ kind: "success", title: t("ldap.saved") }); }
    catch (e) { pushToast({ kind: "error", title: t("ldap.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function runTest() {
    setBusy(true); setResult(null);
    try { setResult({ ok: true, ...(await testLdap({ settings: payload(), username: probe.username || null, password: probe.password || null })) }); }
    catch (e) { setResult({ ok: false, detail: errorMessage(e) }); } finally { setBusy(false); }
  }
  const input = (k, label, extra = {}) => (
    <Field label={label} help={extra.help} hint={extra.hint} error={bad[k] ? t(`ldap.bad.${k}`) : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={form[k]} onChange={set(k)} placeholder={extra.ph} type={extra.type} autoComplete="off" />}</Field>
  );
  const user = result?.utilisateur;
  return (
    <Card title={t("ldap.title")}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("ldap.help")}</p>
      <label className="nx-check" style={{ marginBottom: "var(--space-3)" }}><input type="checkbox" checked={form.enabled} onChange={set("enabled")} /> {t("ldap.enabled")}</label>
      <fieldset className="nx-fs">
        <legend>{t("ldap.server")}</legend>
        <div className="nx-fg">
          {input("url", t("ldap.url"), { ph: "ldaps://dc1.example.org" })}
          {input("base_dn", t("ldap.base"), { ph: "dc=example,dc=org" })}
        </div>
        <div className="nx-checks">
          <label className="nx-check"><input type="checkbox" checked={form.starttls} onChange={set("starttls")} /> {t("ldap.starttls")}</label>
          {bad.starttls && <p className="nx-f-h is-error" role="alert" style={{ margin: 0 }}>{t("ldap.bad.starttls")}</p>}
          <label className="nx-check"><input type="checkbox" checked={form.verify_tls} onChange={set("verify_tls")} /> {t("ldap.verify")}</label>
        </div>
        <Field label={t("ldap.ca")} hint={t("ldap.caHint")}>{(p) => <textarea {...p} className="nx-inp nx-mono nx-notes-input" rows={2} value={form.ca_cert} onChange={set("ca_cert")} placeholder="-----BEGIN CERTIFICATE-----" />}</Field>
      </fieldset>
      <fieldset className="nx-fs">
        <legend>{t("ldap.account")}</legend>
        <div className="nx-fg">
          {input("bind_dn", t("ldap.bindDn"), { ph: "cn=hyperlite,ou=services,dc=example,dc=org", help: t("ldap.bindHelp") })}
          {input("bind_password", t("ldap.bindPw"), { type: "password", hint: saved.bind_password_set ? t("ldap.pwKeep") : null })}
        </div>
      </fieldset>
      <fieldset className="nx-fs">
        <legend>{t("ldap.users")}</legend>
        {input("user_filter", t("ldap.filter"), { help: t("ldap.filterHelp") })}
        <div className="nx-fg">
          {input("group_attribute", t("ldap.groupAttr"), { help: t("ldap.groupAttrHelp") })}
          {input("admin_groups", t("ldap.adminGroups"), { ph: "hyperlite-admins", help: t("ldap.groupsHelp") })}
          {input("allowed_groups", t("ldap.allowedGroups"), { help: t("ldap.allowedHelp") })}
        </div>
      </fieldset>
      <fieldset className="nx-fs">
        <legend>{t("ldap.test")}</legend>
        <div className="nx-fg">
          <Field label={t("ldap.testUser")}>{(p) => <input {...p} className="nx-inp nx-mono" value={probe.username} onChange={(e) => setProbe((x) => ({ ...x, username: e.target.value }))} autoComplete="off" />}</Field>
          <Field label={t("ldap.testPw")}>{(p) => <input {...p} type="password" className="nx-inp" value={probe.password} onChange={(e) => setProbe((x) => ({ ...x, password: e.target.value }))} autoComplete="new-password" />}</Field>
        </div>
        {result && (
          <div className="nx-bn" data-tone={result.ok && (!user || user.accepte) ? "success" : "danger"} role="status">
            <span className="nx-bn-t">{!result.ok ? t("ldap.testKo", { error: result.detail })
              : !user ? t("ldap.testOk") : user.accepte ? t(user.role ? "ldap.userOk" : "ldap.userNotAllowed", { dn: user.dn, role: user.role ? t(`ldap.role.${user.role}`) : "" }) : t("ldap.userKo")}</span>
          </div>
        )}
      </fieldset>
      <div className="nx-fa">
        <button type="button" className="nx-btn" disabled={busy || !form.url || bad.url} onClick={runTest}>{t("ldap.testBtn")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={busy || invalid} onClick={save}>{t("ldap.save")}</button>
      </div>
    </Card>
  );
}
