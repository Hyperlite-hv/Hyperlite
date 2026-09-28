import { useState } from "react";
import { createPortal } from "react-dom";
import { ShieldCheck } from "lucide-react";
import { changeMyPassword } from "../../api/client";
import { useAuthStore } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { SideDrawer, Field } from "./ui";
import { PasswordInput, NewPasswordFields } from "./PasswordFields";
import { passwordAccepted } from "../lib/passwordPolicy";

const EMPTY = { current: "", next: "", confirm: "", code: "" };

// Server answers mapped to a sentence in the user's language (the server speaks English).
export function reason(t, message) {
  if (/Incorrect current password/i.test(message)) return { field: "current", text: t("pw.err.current") };
  if (/2FA/i.test(message)) return { field: "code", text: t("pw.err.code") };
  if (/Too many failed attempts/i.test(message)) return { field: "form", text: t("pw.err.locked") };
  if (/different from the current/i.test(message)) return { field: "next", text: t("pw.err.same") };
  return { field: "next", text: message };
}

// "Change my password", for every signed-in user: current password (+ 2FA code when enabled), new one twice.
// The other sessions of the account are signed out by the server; this one continues with the fresh token.
// Opened from the sidebar, it is portalled into the app root: inside the sidebar it would take the sidebar's
// own (always dark) colours instead of the page theme.
export default function ChangePasswordDrawer({ open, onClose }) {
  const t = useT();
  const { username, totpEnabled, replaceToken } = useAuthStore();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [f, setF] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const close = () => { if (busy) return; setF(EMPTY); setErr(null); onClose(); };
  const valid = f.current.length > 0 && passwordAccepted(f.next, username) && f.confirm === f.next
    && f.next !== f.current && (!totpEnabled || /^\d{6}$/.test(f.code));

  async function submit(e) {
    e?.preventDefault();
    if (!valid || busy) return;
    setBusy(true); setErr(null);
    try {
      const res = await changeMyPassword(f.current, f.next, totpEnabled ? f.code : null);
      replaceToken(res.access_token);
      pushToast({ kind: "success", title: t("pw.changed"), message: t("pw.changedMsg") });
      setF(EMPTY); onClose();
    } catch (e2) {
      setErr(reason(t, e2.message || ""));
    } finally { setBusy(false); }
  }

  if (!open) return null;
  return createPortal(
    <SideDrawer open={open} title={t("pw.changeTitle")} onClose={close} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={close} disabled={busy}>{t("action.cancel")}</button>
      <button type="submit" form="nx-change-pw" className="nx-btn nx-btn--primary" disabled={!valid || busy}>{busy ? t("pw.saving") : t("pw.changeBtn")}</button>
    </>}>
      <form id="nx-change-pw" className="nx-stack" onSubmit={submit} noValidate>
        <p className="nx-pwnote"><ShieldCheck size={16} aria-hidden="true" />{t("pw.changeNote")}</p>
        {err?.field === "form" && <div className="nx-bn" data-tone="warning" role="alert"><span className="nx-bn-t">{err.text}</span></div>}
        <PasswordInput label={t("pw.current")} value={f.current} onChange={(v) => setF({ ...f, current: v })} autoComplete="current-password" error={err?.field === "current" ? err.text : null} />
        <NewPasswordFields label={t("pw.new")} username={username} value={f.next} onChange={(v) => setF((s) => ({ ...s, next: v }))}
          confirm={f.confirm} onConfirm={(v) => setF((s) => ({ ...s, confirm: v }))} error={err?.field === "next" ? err.text : null} />
        {totpEnabled && (
          <Field label={t("pw.code")} hint={t("pw.codeHint")} error={err?.field === "code" ? err.text : null}>
            {(p) => <input {...p} className="nx-inp nx-mono" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.replace(/\D/g, "") })} />}
          </Field>
        )}
      </form>
    </SideDrawer>,
    document.querySelector(".nx-root") || document.body,
  );
}
