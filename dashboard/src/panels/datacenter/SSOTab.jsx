import { useEffect, useState } from "react";
import { KeyRound, Save } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchSsoConfig, updateSsoConfig } from "../../api/client";

const EMPTY = {
  enabled: false, issuer: "", client_id: "", client_secret: "", redirect_uri: "",
  scope: "openid profile email groups", group_claim: "groups", admin_groups: "",
};

// Chantier 20 (SSO OIDC, 2026-09-17) : configuration d'un fournisseur
// d'identite externe, reglage d'infrastructure serveur (comme les
// notifications ou le pare-feu reseau) donc ici, dans Datacenter, PAS
// dans la modale "Securite du compte" (chantier 30, libre-service par
// utilisateur) -- celle-ci reste pour le 2FA/jetons API de CHAQUE
// utilisateur, pas pour la config globale du serveur.
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
      .catch((e) => pushToast({ kind: "error", title: "Erreur", message: e.message }))
      .finally(() => setLoading(false));
  }, [pushToast]);

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const payload = { ...form, client_secret: form.client_secret || null };
      await updateSsoConfig(payload);
      pushToast({ kind: "success", title: "Configuration SSO enregistrée" });
      if (form.client_secret) setSecretSet(true);
      setForm((f) => ({ ...f, client_secret: "" }));
    } catch (err) {
      pushToast({ kind: "error", title: "Échec de l'enregistrement", message: err.message });
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="px-4 py-3 text-sm text-anthracite-400">Chargement...</div>;

  return (
    <div className="space-y-5">
      <div className="card">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-anthracite-600">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-anthracite-100">
            <KeyRound size={15} /> Authentification unique (SSO OIDC)
          </h3>
          <button
            type="button"
            onClick={() => setForm((f) => ({ ...f, enabled: !f.enabled }))}
            className={`rounded-full px-3 py-1 text-xs font-medium border ${form.enabled ? "border-status-ok bg-status-ok/10 text-status-ok" : "border-anthracite-600 text-anthracite-300"}`}
          >
            {form.enabled ? "Activé" : "Désactivé"}
          </button>
        </div>

        <p className="px-4 pt-3 text-xs text-anthracite-400">
          L'authentification locale par mot de passe reste toujours possible en parallèle
          (secours si l'IdP est injoignable ou mal configuré) — le SSO s'ajoute, il ne la remplace jamais.
        </p>

        <form onSubmit={handleSave} className="space-y-3 px-4 py-4">
          <div>
            <label className="text-xs font-medium text-anthracite-300">Issuer (URL de découverte OIDC)</label>
            <input
              className="input mt-1" value={form.issuer}
              onChange={(e) => setForm({ ...form, issuer: e.target.value })}
              placeholder="https://mon-idp.example.com/realms/hyperlite"
            />
            <p className="mt-1 text-[11px] text-anthracite-500">
              Hyperlite récupère automatiquement les URLs d'autorisation/jeton/clés depuis
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
                Client secret {secretSet && <span className="text-anthracite-500">(déjà enregistré)</span>}
              </label>
              <input
                type="password" className="input mt-1" value={form.client_secret}
                onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
                placeholder={secretSet ? "laisser vide pour conserver" : ""}
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-medium text-anthracite-300">URL de retour (redirect_uri)</label>
            <input
              className="input mt-1" value={form.redirect_uri}
              onChange={(e) => setForm({ ...form, redirect_uri: e.target.value })}
              placeholder="https://mon-serveur:8000/auth/sso/callback"
            />
            <p className="mt-1 text-[11px] text-anthracite-500">
              Doit être enregistrée à l'identique côté IdP, et joignable depuis le navigateur de l'utilisateur.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-anthracite-300">Claim des groupes</label>
              <input className="input mt-1" value={form.group_claim} onChange={(e) => setForm({ ...form, group_claim: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-anthracite-300">Groupes IdP → rôle admin</label>
              <input
                className="input mt-1" value={form.admin_groups}
                onChange={(e) => setForm({ ...form, admin_groups: e.target.value })}
                placeholder="hyperlite-admins, infra-team"
              />
            </div>
          </div>
          <p className="text-[11px] text-anthracite-500">
            Tout utilisateur SSO appartenant à l'un de ces groupes (séparés par des virgules) reçoit le rôle
            admin — les autres reçoivent observateur. Réévalué à chaque connexion.
          </p>

          <div className="flex justify-end">
            <button type="submit" disabled={saving} className="btn-primary">
              <Save size={14} /> {saving ? "Enregistrement..." : "Enregistrer"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
