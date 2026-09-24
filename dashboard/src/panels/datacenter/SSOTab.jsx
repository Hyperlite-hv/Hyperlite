import LoadingState from "../../components/LoadingState";
import { useEffect, useState } from "react";
import { KeyRound, Save } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchSsoConfig, updateSsoConfig } from "../../api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

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

  if (loading) return <div className="px-4 py-3 text-sm text-muted-foreground"><LoadingState /></div>;

  return (
    <div className="space-y-5">
      <Card className="p-0">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <KeyRound size={15} /> Single sign-on (OIDC SSO)
          </h3>
          <Label className="flex items-center gap-2 text-xs font-medium text-foreground/80">
            {form.enabled ? "Enabled" : "Disabled"}
            <Switch checked={form.enabled} onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))} />
          </Label>
        </div>

        <p className="px-4 pt-3 text-xs text-muted-foreground">
          Local password authentication always remains possible in parallel (fallback if the IdP is unreachable or misconfigured): SSO is added on top, it never replaces it.
        </p>

        <form onSubmit={handleSave} className="space-y-3 px-4 py-4">
          <div>
            <Label className="text-xs font-medium text-foreground/80">Issuer (OIDC discovery URL)</Label>
            <Input aria-label="Issuer (OIDC discovery URL)"
              className="mt-1" value={form.issuer}
              onChange={(e) => setForm({ ...form, issuer: e.target.value })}
              placeholder="https://idp.example.com/realms/hyperlite"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Hyperlite automatically fetches the authorization/token/keys URLs from
              {" "}<code>{"{issuer}"}/.well-known/openid-configuration</code>.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs font-medium text-foreground/80">Client ID</Label>
              <Input aria-label="Client ID" className="mt-1" value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
            </div>
            <div>
              <Label className="text-xs font-medium text-foreground/80">
                Client secret {secretSet && <span className="text-muted-foreground">(already saved)</span>}
              </Label>
              <Input aria-label="Client secret"
                type="password" className="mt-1" value={form.client_secret}
                onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                placeholder={secretSet ? "leave empty to keep the current one" : ""}
              />
            </div>
          </div>

          <div>
            <Label className="text-xs font-medium text-foreground/80">Redirect URL (redirect_uri)</Label>
            <Input aria-label="Redirect URL (redirect_uri)"
              className="mt-1" value={form.redirect_uri}
              onChange={(e) => setForm({ ...form, redirect_uri: e.target.value })}
              placeholder="https://hyperlite.example.com:8000/auth/sso/callback"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Must be registered identically on the IdP side, and reachable from the user's browser.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs font-medium text-foreground/80">Groups claim</Label>
              <Input aria-label="Groups claim" className="mt-1" value={form.group_claim} onChange={(e) => setForm({ ...form, group_claim: e.target.value })} />
            </div>
            <div>
              <Label className="text-xs font-medium text-foreground/80">IdP groups → admin role</Label>
              <Input aria-label="IdP groups → admin role"
                className="mt-1" value={form.admin_groups}
                onChange={(e) => setForm({ ...form, admin_groups: e.target.value })}
                placeholder="hyperlite-admins, infra-team"
              />
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Any SSO user belonging to one of these groups (comma-separated) gets the admin role, and everyone else gets observateur. Re-evaluated at every sign-in.
          </p>

          <div className="flex justify-end">
            <Button type="submit" disabled={saving}>
              <Save /> {saving ? "Saving..." : "Save"}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
