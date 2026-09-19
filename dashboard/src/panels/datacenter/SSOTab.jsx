import { useEffect, useState } from "react";
import { KeyRound, Save } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchSsoConfig, updateSsoConfig } from "../../api/client";

const EMPTY = {
  enabled: false, issuer: "", client_id: "", client_secret: "", redirect_uri: "",
  scope: "openid profile email groups", group_claim: "groups", admin_groups: "",
};

// OIDC SSO: configuration of an external identity provider. It is a server
// infrastructure setting (like notifications or the network firewall), so it
// lives here in Datacenter, NOT in the "Account security" modal (self-service,
// per user), which stays for the 2FA/API tokens of EACH user, not for the global
// server configuration.
export default function SSOTab() {
  const pushToast = useInfraStore((s) => s.pushToast);
  const [form, setForm] = useState(EMPTY);
  const [secretSet, setSecretSet] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchSsoConfig()
      .then((c) => {
        setForm({ ...EMPTY, ...c, client_secret: "" });
        setSecretSet(!!c.client_secret_set);
      })
      .catch((e) => pushToast({ kind: "error", title: "Error", message: e.message }))
      .finally(() => setLoading(false));
  }, [pushToast]);

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const payload = { ...form, client_secret: form.client_secret || null };
      await updateSsoConfig(payload);
      pushToast({ kind: "success", title: "SSO configuration saved" });
      if (form.client_secret) setSecretSet(true);
      setForm((f) => ({ ...f, client_secret: "" }));
    } catch (err) {
      pushToast({ kind: "error", title: "Save failed", message: err.message });
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="px-4 py-3 text-sm text-anthracite-400">Loading...</div>;

  return (
    <div className="space-y-5">
      <div className="card">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-anthracite-600">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-anthracite-100">
            <KeyRound size={15} /> Single sign-on (OIDC SSO)
          </h3>
          <button
            type="button"
            onClick={() => setForm((f) => ({ ...f, enabled: !f.enabled }))}
            className={`rounded-full px-3 py-1 text-xs font-medium border ${form.enabled ? "border-status-ok bg-status-ok/10 text-status-ok" : "border-anthracite-600 text-anthracite-300"}`}
          >
            {form.enabled ? "Enabled" : "Disabled"}
          </button>
        </div>

        <p className="px-4 pt-3 text-xs text-anthracite-400">
          Local password authentication always remains possible in parallel (fallback if the IdP is unreachable or misconfigured): SSO is added on top, it never replaces it.
        </p>

        <form onSubmit={handleSave} className="space-y-3 px-4 py-4">
          <div>
            <label className="text-xs font-medium text-anthracite-300">Issuer (OIDC discovery URL)</label>
            <input
              className="input mt-1" value={form.issuer}
              onChange={(e) => setForm({ ...form, issuer: e.target.value })}
              placeholder="https://idp.example.com/realms/hyperlite"
            />
            <p className="mt-1 text-[11px] text-anthracite-500">
              Hyperlite automatically fetches the authorization/token/keys URLs from
              {" "}<code>{"{issuer}"}/.well-known/openid-configuration</code>.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-anthracite-300">Client ID</label>
              <input className="input mt-1" value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-anthracite-300">
                Client secret {secretSet && <span className="text-anthracite-500">(already saved)</span>}
              </label>
              <input
                type="password" className="input mt-1" value={form.client_secret}
                onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                placeholder={secretSet ? "leave empty to keep the current one" : ""}
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-medium text-anthracite-300">Redirect URL (redirect_uri)</label>
            <input
              className="input mt-1" value={form.redirect_uri}
              onChange={(e) => setForm({ ...form, redirect_uri: e.target.value })}
              placeholder="https://hyperlite.example.com:8000/auth/sso/callback"
            />
            <p className="mt-1 text-[11px] text-anthracite-500">
              Must be registered identically on the IdP side, and reachable from the user's browser.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-anthracite-300">Groups claim</label>
              <input className="input mt-1" value={form.group_claim} onChange={(e) => setForm({ ...form, group_claim: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-anthracite-300">IdP groups → admin role</label>
              <input
                className="input mt-1" value={form.admin_groups}
                onChange={(e) => setForm({ ...form, admin_groups: e.target.value })}
                placeholder="hyperlite-admins, infra-team"
              />
            </div>
          </div>
          <p className="text-[11px] text-anthracite-500">
            Any SSO user belonging to one of these groups (comma-separated) gets the admin role, and everyone else gets observateur. Re-evaluated at every sign-in.
          </p>

          <div className="flex justify-end">
            <button type="submit" disabled={saving} className="btn-primary">
              <Save size={14} /> {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
