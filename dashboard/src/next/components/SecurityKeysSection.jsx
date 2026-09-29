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
  const authSource = useAuthStore((s) => s.authSource);
  const [keys, setKeys] = useState(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const supported = webauthnSupported();
  const reload = () => fetchSecurityKeys().then(setKeys).catch(() => setKeys([]));
  useEffect(() => { if (open) { reload(); setName(""); } }, [open]);

  async function add(e) {
    e.preventDefault();
    // The password is asked again, as for removing a key: a session left open must not be enough to add one.
    let password = "";
    if (authSource !== "sso") {
      password = await promptText({ title: t("sk.addTitle"), label: t("sk.password"), type: "password", confirmLabel: t("sk.add"), validate: (v) => (v ? "" : t("sk.passwordRequired")) });
      if (!password) return;
    }
    setBusy(true);
    try {
      const options = await securityKeyOptions(password);
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
    <section className="nx-sk" aria-label={t("sk.title")}>
      <h4 className="nx-sk-h"><Fingerprint size={15} aria-hidden="true" /> {t("sk.title")}</h4>
      <p className="nx-f-h">{t("sk.help", { host: window.location.hostname })}</p>
      {!supported && <p className="nx-f-h is-warning" role="note">{t("sk.unsupported")}</p>}
      <form onSubmit={add} className="nx-sk-form">
        <label className="nx-sk-label">{t("sk.name")}
          <input className="nx-inp" aria-label={t("sk.name")} placeholder={t("sk.namePlaceholder")} maxLength={64} value={name} onChange={(e) => setName(e.target.value)} disabled={!supported} />
        </label>
        <button type="submit" className="nx-btn" disabled={busy || !supported}><Plus size={14} aria-hidden="true" />{t("sk.add")}</button>
      </form>
      <div className="nx-sk-list">
        {keys && keys.length === 0 && <div className="nx-sk-empty">{t("sk.none")}</div>}
        {keys && keys.map((k) => (
          <div key={k.id} className="nx-sk-row">
            <div className="nx-sk-main">
              <div className="nx-sk-name">{k.nom}</div>
              <div className="nx-f-h">{k.rp_id} · {t("sk.added_on", { date: formatDateTime(k.cree_le, lang) })} · {k.utilise_le ? t("sk.used_on", { date: formatDateTime(k.utilise_le, lang) }) : t("sk.never")}</div>
            </div>
            <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("sk.removeAria", { name: k.nom })} onClick={() => remove(k)}><Trash2 size={14} aria-hidden="true" /></button>
          </div>
        ))}
      </div>
    </section>
  );
}
