import { useEffect, useMemo, useState } from "react";
import { fetchSsoConfig, updateSsoConfig } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";

const EMPTY = { enabled: false, issuer: "", client_id: "", client_secret: "", redirect_uri: "", scope: "openid profile email groups", group_claim: "groups", admin_groups: "" };
const FIELDS = ["enabled", "issuer", "client_id", "redirect_uri", "scope", "group_claim", "admin_groups"];

const isUrl = (v, https = false) => { try { const u = new URL(v); return https ? u.protocol === "https:" : /^https?:$/.test(u.protocol); } catch { return false; } };

// OIDC single sign-on (server-wide setting). Local password sign-in always stays available as a fallback.
// The backend accepts any content, so the page prevents enabling an incomplete or malformed configuration
// (which would break the SSO button on the login screen) and shows unsaved changes.
export default function SsoPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [saved, setSaved] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [secretSet, setSecretSet] = useState(false);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = () => fetchSsoConfig().then((c) => {
    const f = { ...EMPTY, ...c, enabled: !!c.enabled, client_secret: "" }; // the API stores 0/1
    setSaved(f); setForm(f); setSecretSet(!!c.client_secret_set); setError(null);
  }).catch((e) => setError(errorMessage(e)));
  useEffect(() => { load(); }, []);

  const dirty = useMemo(() => !!saved && (FIELDS.some((k) => form[k] !== saved[k]) || form.client_secret !== ""), [form, saved]);
  const problems = useMemo(() => {
    const p = {};
    if (form.issuer && !isUrl(form.issuer)) p.issuer = "sso.badUrl";
    else if (form.issuer && !isUrl(form.issuer, true)) p.issuer = "sso.httpsAdvice";
    if (form.redirect_uri && !isUrl(form.redirect_uri)) p.redirect_uri = "sso.badUrl";
    if (form.enabled) {
      if (!form.issuer) p.issuer = "sso.required";
      if (!form.client_id) p.client_id = "sso.required";
      if (!form.redirect_uri) p.redirect_uri = "sso.required";
      if (!secretSet && !form.client_secret) p.client_secret = "sso.required";
    }
    return p;
  }, [form, secretSet]);
  // an https advice is a warning, everything else blocks saving
  const blocking = Object.entries(problems).filter(([, v]) => v !== "sso.httpsAdvice");
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save(e) {
    e.preventDefault();
    if (blocking.length) return;
    if (form.enabled && !saved.enabled && !form.admin_groups.trim()
      && !(await confirmAction({ title: t("sso.noAdminTitle"), message: t("sso.noAdminMsg"), confirmLabel: t("sso.enable") }))) return;
    setSaving(true);
    try {
      await updateSsoConfig({ ...form, client_secret: form.client_secret || null });
      pushToast({ kind: "success", title: t("sso.saved") });
      await load();
    } catch (err) { pushToast({ kind: "error", title: t("sso.saveFailed"), message: errorMessage(err) }); }
    finally { setSaving(false); }
  }

  if (error && !saved) return <ErrorState message={error} onRetry={load} />;
  if (!saved) return <p className="nx-muted" role="status">{t("loading")}</p>;

  const field = (k, label, props = {}) => (
    <label>{label}
      <input className="nx-input" aria-label={props.aria} aria-invalid={problems[k] && problems[k] !== "sso.httpsAdvice" ? true : undefined} aria-describedby={problems[k] ? `sso-${k}-msg` : undefined}
        value={form[k]} onChange={set(k)} autoComplete="off" {...props.input} />
      {problems[k] && <span id={`sso-${k}-msg`} className={problems[k] === "sso.httpsAdvice" ? "nx-hint" : "nx-hint nx-hint--error"}>{t(problems[k])}</span>}
      {props.help && <span className="nx-hint">{props.help}</span>}
    </label>
  );

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="sso-title">
        <div className="nx-cardhead">
          <h2 id="sso-title">{t("sso.title")}</h2>
          <label className="nx-check"><input type="checkbox" role="switch" aria-label={t("sso.enabled")} checked={form.enabled} onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))} /> {form.enabled ? t("sso.enabled") : t("sso.disabled")}</label>
        </div>
        <p className="nx-notice">{t("sso.fallback")}</p>
        <form className="nx-form" onSubmit={save} noValidate>
          {field("issuer", t("sso.issuer"), { aria: "Issuer (OIDC discovery URL)", input: { placeholder: "https://idp.example.com/realms/hyperlite", inputMode: "url" }, help: <>{t("sso.issuerHelp")} <code className="nx-mono">{"{issuer}"}/.well-known/openid-configuration</code></> })}
          <div className="nx-formgrid">
            {field("client_id", "Client ID", { aria: "Client ID" })}
            <label>{t("sso.secret")} {secretSet && <span className="nx-muted">({t("sso.secretSet")})</span>}
              <input className="nx-input" aria-label="Client secret" type="password" autoComplete="new-password" value={form.client_secret} onChange={set("client_secret")} placeholder={secretSet ? t("sso.secretKeep") : ""} aria-invalid={problems.client_secret ? true : undefined} />
              {problems.client_secret && <span className="nx-hint nx-hint--error">{t(problems.client_secret)}</span>}
            </label>
          </div>
          {field("redirect_uri", t("sso.redirect"), { aria: "Redirect URL (redirect_uri)", input: { placeholder: "https://hyperlite.example.com:8000/auth/sso/callback", inputMode: "url" }, help: t("sso.redirectHelp") })}
          {field("scope", t("sso.scope"), { aria: "Scopes", help: t("sso.scopeHelp") })}
          <div className="nx-formgrid">
            {field("group_claim", t("sso.groupClaim"), { aria: "Groups claim" })}
            {field("admin_groups", t("sso.adminGroups"), { aria: "IdP groups → admin role", input: { placeholder: "hyperlite-admins, infra-team" } })}
          </div>
          <span className="nx-hint">{t("sso.adminGroupsHelp")}</span>
          <div className="nx-formactions">
            {dirty && <span className="nx-muted" role="status">{t("sso.unsaved")}</span>}
            <button type="button" className="nx-btn" disabled={!dirty || saving} onClick={() => setForm(saved)}>{t("sso.discard")}</button>
            <button type="submit" className="nx-btn nx-btn--primary" disabled={saving || !dirty || blocking.length > 0}>{saving ? t("sso.saving") : t("sso.save")}</button>
          </div>
        </form>
      </section>
    </div>
  );
}
