import { useEffect, useState } from "react";
import { Fingerprint, Plus, Trash2 } from "lucide-react";
import { fetchSecurityKeys, securityKeyOptions, registerSecurityKey, deleteSecurityKey } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { promptText } from "../../store/usePromptStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { createCredential, webauthnSupported } from "../lib/webauthn";

// Security keys and passkeys of the signed-in account (Account security window). A key is bound to the host
// name the dashboard is opened with; removing one asks for the password.
export default function SecurityKeysSection({ open }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const refreshMe = useAuthStore((s) => s.refreshMe);
  const [keys, setKeys] = useState(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const supported = webauthnSupported();
  const reload = () => fetchSecurityKeys().then(setKeys).catch(() => setKeys([]));
  useEffect(() => { if (open) { reload(); setName(""); } }, [open]);

  async function add(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const options = await securityKeyOptions();
      const credential = await createCredential(options);
      const r = await registerSecurityKey(credential, name.trim());
      setKeys(r.cles); setName("");
      pushToast({ kind: "success", title: t("sk.added"), message: t("sk.addedMsg") });
      await refreshMe();
    } catch (er) {
      pushToast({ kind: "error", title: t("sk.addFailed"), message: er.name === "NotAllowedError" ? t("login.keyCancelled") : errorMessage(er) });
    } finally { setBusy(false); }
  }
  async function remove(k) {
    const password = await promptText({ title: t("sk.removeTitle", { name: k.nom }), label: t("sk.password"), type: "password", confirmLabel: t("sk.remove"), validate: (v) => (v ? "" : t("sk.passwordRequired")) });
    if (!password) return;
    try { const r = await deleteSecurityKey(k.id, password); setKeys(r.cles); pushToast({ kind: "success", title: t("sk.removed"), message: k.nom }); await refreshMe(); }
    catch (er) { pushToast({ kind: "error", title: t("sk.removeFailed"), message: errorMessage(er) }); }
  }

  return (
    <section className="space-y-3 border-t border-border pt-4" aria-label={t("sk.title")}>
      <h4 className="flex items-center gap-2 text-sm font-semibold text-foreground"><Fingerprint size={15} aria-hidden="true" /> {t("sk.title")}</h4>
      <p className="text-xs text-muted-foreground">{t("sk.help", { host: window.location.hostname })}</p>
      {!supported && <p className="text-xs text-status-warning" role="note">{t("sk.unsupported")}</p>}
      <form onSubmit={add} className="flex items-end gap-2">
        <label className="flex-1 text-xs font-medium text-foreground/80">{t("sk.name")}
          <input className="nx-inp mt-1 w-full" aria-label={t("sk.name")} placeholder={t("sk.namePlaceholder")} maxLength={64} value={name} onChange={(e) => setName(e.target.value)} disabled={!supported} />
        </label>
        <button type="submit" className="nx-btn" disabled={busy || !supported}><Plus size={14} aria-hidden="true" />{t("sk.add")}</button>
      </form>
      <div className="divide-y divide-border rounded-md border border-border">
        {keys && keys.length === 0 && <div className="px-3 py-3 text-xs text-muted-foreground text-center">{t("sk.none")}</div>}
        {keys && keys.map((k) => (
          <div key={k.id} className="flex items-center gap-3 px-3 py-2 text-sm">
            <div className="min-w-0 flex-1">
              <div className="text-foreground truncate">{k.nom}</div>
              <div className="text-muted-foreground text-xs">{k.rp_id} · {t("sk.added_on", { date: formatDateTime(k.cree_le, lang) })} · {k.utilise_le ? t("sk.used_on", { date: formatDateTime(k.utilise_le, lang) }) : t("sk.never")}</div>
            </div>
            <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("sk.removeAria", { name: k.nom })} onClick={() => remove(k)}><Trash2 size={14} aria-hidden="true" /></button>
          </div>
        ))}
      </div>
    </section>
  );
}
