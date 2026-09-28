import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import "./next.css";
import "./refonte.css";
import { changeMyPassword } from "../api/client";
import { useAuthStore } from "../store/useAuthStore";
import EnclaveMark from "../components/EnclaveMark";
import { useT, useLangStore } from "./i18n";
import { useThemeStore } from "./tokens/theme";
import { Field } from "./components/ui";
import { PasswordInput, NewPasswordFields } from "./components/PasswordFields";
import { reason } from "./components/ChangePasswordDrawer";
import { passwordAccepted } from "./lib/passwordPolicy";

const EMPTY = { current: "", next: "", confirm: "", code: "" };

// Shown instead of the dashboard when the server says the password no longer meets the policy (a password set
// before the policy existed): until it is changed, the server refuses every other request. Same layout as the
// sign-in screen, and the same change as "Change my password".
export default function NextPasswordRequired() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const initTheme = useThemeStore((s) => s.init);
  const { username, totpEnabled, replaceToken, logout } = useAuthStore();
  const [f, setF] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.ui = "next";
    root.lang = lang;
    const cleanup = initTheme();
    return () => cleanup?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const valid = f.current.length > 0 && passwordAccepted(f.next, username) && f.confirm === f.next
    && f.next !== f.current && (!totpEnabled || /^\d{6}$/.test(f.code));

  async function submit(e) {
    e.preventDefault();
    if (!valid || busy) return;
    setBusy(true); setErr(null);
    try {
      const res = await changeMyPassword(f.current, f.next, totpEnabled ? f.code : null);
      replaceToken(res.access_token);
    } catch (e2) {
      setErr(reason(t, e2.message || ""));
      setBusy(false);
    }
  }

  return (
    <div className="nx-login">
      <div className="nx-login-l">
        <EnclaveMark size={44} rails="var(--color-text-primary)" core="var(--color-accent)" />
        <b>{t("app.name")}</b>
        <p>{t("login.tagline")}</p>
      </div>
      <div className="nx-login-r">
        <form className="nx-login-box nx-login-box--wide" onSubmit={submit} noValidate>
          <h1><KeyRound size={20} aria-hidden="true" /> {t("pw.required")}</h1>
          <p className="nx-muted" style={{ margin: 0 }}>{t("pw.requiredHelp")}</p>
          {err?.field === "form" && <div className="nx-bn" data-tone="warning" role="alert"><span className="nx-bn-t">{err.text}</span></div>}
          <PasswordInput label={t("pw.current")} value={f.current} onChange={(v) => setF((s) => ({ ...s, current: v }))} autoComplete="current-password" error={err?.field === "current" ? err.text : null} />
          <NewPasswordFields label={t("pw.new")} username={username} value={f.next} onChange={(v) => setF((s) => ({ ...s, next: v }))}
            confirm={f.confirm} onConfirm={(v) => setF((s) => ({ ...s, confirm: v }))} error={err?.field === "next" ? err.text : null} />
          {totpEnabled && (
            <Field label={t("pw.code")} hint={t("pw.codeHint")} error={err?.field === "code" ? err.text : null}>
              {(p) => <input {...p} className="nx-inp nx-mono" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={f.code} onChange={(e) => setF((s) => ({ ...s, code: e.target.value.replace(/\D/g, "") }))} />}
            </Field>
          )}
          <button type="submit" className="nx-btn nx-btn--primary nx-login-btn" disabled={!valid || busy}>{busy ? t("pw.saving") : t("pw.requiredBtn")}</button>
          <button type="button" className="nx-btn nx-btn--ghost nx-login-btn" onClick={logout} disabled={busy}>{t("top.signout")}</button>
        </form>
      </div>
    </div>
  );
}
