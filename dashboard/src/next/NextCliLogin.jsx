import { useEffect, useState } from "react";
import { CheckCircle2, Laptop, XCircle } from "lucide-react";
import "./next.css";
import "./refonte.css";
import { useAuthStore } from "../store/useAuthStore";
import { fetchCliRequest, decideCliRequest } from "../api/client";
import EnclaveMark from "../components/EnclaveMark";
import { useT, useLangStore } from "./i18n";
import { useThemeStore } from "./tokens/theme";
import { errorMessage } from "./lib/errors";
import { formatDateTime } from "./lib/format";
import NextLogin from "./NextLogin";
import { Loading } from "./components/ui";

// /cli-login?code=XXXX-XXXX: approval of a `hyperlite login` from this web session, so the sign-in of the
// workstation goes through the usual screen (password and 2FA, or SSO). The code shown here must match the one
// printed in the terminal: that comparison is what protects against a link sent by someone else.
export default function NextCliLogin() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const initTheme = useThemeStore((s) => s.init);
  const status = useAuthStore((s) => s.status);
  const restoreSession = useAuthStore((s) => s.restoreSession);
  const [code, setCode] = useState(() => new URLSearchParams(window.location.search).get("code") || "");
  const [typed, setTyped] = useState("");
  const [req, setReq] = useState(null);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null); // "approved" | "denied"
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.ui = "next";
    root.lang = lang;
    document.title = t("cli.pageTitle");
    const cleanup = initTheme();
    return () => cleanup?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { restoreSession(); }, [restoreSession]);
  useEffect(() => {
    if (status !== "authenticated" || !code) return;
    setError(null); setReq(null);
    fetchCliRequest(code).then(setReq).catch((e) => setError(errorMessage(e)));
  }, [code, status]);

  if (status === "anonymous") return <NextLogin />;

  async function decide(approve) {
    setBusy(true);
    try { await decideCliRequest(code, approve); setDone(approve ? "approved" : "denied"); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  let body;
  if (done) {
    body = (
      <div className="nx-cli-result" role="status">
        {done === "approved" ? <CheckCircle2 size={28} aria-hidden="true" className="nx-tone-success" /> : <XCircle size={28} aria-hidden="true" />}
        <h1>{t(done === "approved" ? "cli.approvedTitle" : "cli.deniedTitle")}</h1>
        <p className="nx-muted">{t(done === "approved" ? "cli.approvedHelp" : "cli.deniedHelp")}</p>
      </div>
    );
  } else if (!code) {
    body = (
      <form onSubmit={(e) => { e.preventDefault(); setCode(typed.trim()); }} className="nx-stack">
        <h1>{t("cli.title")}</h1>
        <p className="nx-muted" style={{ margin: 0 }}>{t("cli.typeCode")}</p>
        <input className="nx-inp nx-mono nx-login-code" aria-label={t("cli.code")} autoFocus value={typed} onChange={(e) => setTyped(e.target.value.toUpperCase())} placeholder="XXXX-XXXX" />
        <button type="submit" className="nx-btn nx-btn--primary nx-login-btn" disabled={typed.replace(/[^A-Z]/g, "").length !== 8}>{t("cli.continue")}</button>
      </form>
    );
  } else if (error) {
    body = (
      <div className="nx-stack">
        <h1>{t("cli.title")}</h1>
        <p role="alert" className="nx-f-h is-error" style={{ margin: 0 }}>{t("cli.invalid")}</p>
        <p className="nx-muted" style={{ margin: 0 }}>{error}</p>
      </div>
    );
  } else if (!req) {
    body = <Loading />;
  } else {
    body = (
      <div className="nx-stack">
        <h1>{t("cli.title")}</h1>
        <p className="nx-muted" style={{ margin: 0 }}>{t("cli.lead")}</p>
        <div className="nx-cli-code" aria-label={t("cli.code")}>{req.user_code}</div>
        <p className="nx-muted" style={{ margin: 0 }}>{t("cli.compare")}</p>
        <dl className="nx-dl2">
          <dt>{t("cli.workstation")}</dt><dd className="nx-mono"><Laptop size={14} aria-hidden="true" /> {req.hostname}</dd>
          <dt>{t("cli.address")}</dt><dd className="nx-mono">{req.source_ip}</dd>
          <dt>{t("cli.requested")}</dt><dd>{formatDateTime(req.created_at, lang)}</dd>
          <dt>{t("cli.validity")}</dt><dd>{t("cli.days", { n: req.token_days })}</dd>
        </dl>
        <div className="nx-inline">
          <button type="button" className="nx-btn nx-btn--primary" disabled={busy} onClick={() => decide(true)}>{t("cli.approve")}</button>
          <button type="button" className="nx-btn" disabled={busy} onClick={() => decide(false)}>{t("cli.deny")}</button>
        </div>
        <p className="nx-muted" style={{ margin: 0, fontSize: "var(--fs-12)" }}>{t("cli.revokeHint")}</p>
      </div>
    );
  }

  return (
    <div className="nx-login">
      <div className="nx-login-l">
        <EnclaveMark size={44} rails="var(--color-text-primary)" core="var(--color-accent)" />
        <b>{t("app.name")}</b>
        <p>{t("cli.tagline")}</p>
      </div>
      <div className="nx-login-r"><div className="nx-login-box">{body}</div></div>
    </div>
  );
}
