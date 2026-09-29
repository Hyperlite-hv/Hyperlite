import { useCallback, useEffect, useState } from "react";
import { fetchVMCloudInit, setVMCloudInit } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState } from "../components/States";
import { Card, Field, Loading } from "../components/ui";

const USER_RE = /^[a-z_][a-z0-9_-]{0,31}$/;
const KEY_RE = /^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-nistp(256|384|521)|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com) [A-Za-z0-9+/=]+( .{0,200})?$/;
const keysOf = (text) => [...new Set(text.split("\n").map((l) => l.trim().split(/\s+/).join(" ")).filter(Boolean))];

// The account of a VM made from a cloud image: its SSH keys and password, applied by cloud-init at the VM's next
// boot. The password is never shown (Hyperlite does not keep it): an empty field leaves it as it is.
export default function VmCloudInitPage({ resource: vm }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [user, setUser] = useState("");
  const [keys, setKeys] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const s = await fetchVMCloudInit(vm.nom);
      setState(s); setUser(s.utilisateur || ""); setKeys((s.cles_ssh || []).join("\n")); setPassword(""); setError(null);
    } catch (e) { setError(errorMessage(e)); }
  }, [vm.nom]);
  useEffect(() => { load(); }, [load]);
  if (error && !state) return <ErrorState message={error} onRetry={load} />;
  if (!state) return <Loading />;
  if (!state.disponible) return <Card title={t("ci.title")}><p className="nx-muted" style={{ margin: 0 }}>{t("ci.none")}</p></Card>;

  const list = keysOf(keys);
  const bad = {
    user: !USER_RE.test(user),
    keys: list.find((k) => !KEY_RE.test(k)),
    password: password !== "" && password.length < 8,
  };
  const invalid = bad.user || Boolean(bad.keys) || bad.password;
  const dirty = user !== (state.utilisateur || "") || list.join("\n") !== (state.cles_ssh || []).join("\n") || password !== "";
  async function save() {
    if (invalid) return;
    setBusy(true);
    try {
      const s = await setVMCloudInit(vm.nom, { utilisateur: user, cles_ssh: list, mot_de_passe: password || null });
      setState(s); setPassword("");
      pushToast({ kind: "success", title: t("ci.saved"), message: t(s.en_marche ? "ci.nextBootRunning" : "ci.nextBoot") });
    } catch (e) { pushToast({ kind: "error", title: t("ci.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  return (
    <Card title={t("ci.title")} note={state.modifie_le ? t("ci.lastChange", { d: formatDateTime(state.modifie_le, lang) }) : null}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-4)", fontSize: "var(--fs-13)" }}>{t("ci.help")}</p>
      <div className="nx-stack" style={{ gap: "var(--space-3)" }}>
        <Field label={t("ci.user")} error={bad.user ? t("ci.userRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" disabled={!caps.admin} value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" />}</Field>
        <Field label={t("ci.keys")} error={bad.keys ? t("ci.keyRule", { key: bad.keys.slice(0, 32) }) : null} hint={t("ci.keysHelp")}>{(p) => <textarea {...p} className="nx-inp nx-mono nx-notes-input" rows={4} disabled={!caps.admin} value={keys} onChange={(e) => setKeys(e.target.value)} placeholder="ssh-ed25519 AAAA… user@host" />}</Field>
        <Field label={t("ci.password")} error={bad.password ? t("ci.passwordRule") : null} hint={t("ci.passwordHelp")}>{(p) => <input {...p} type="password" className="nx-inp" disabled={!caps.admin} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />}</Field>
      </div>
      {caps.admin && <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" disabled={!dirty || invalid || busy} onClick={save}>{t("ci.save")}</button></div>}
    </Card>
  );
}
