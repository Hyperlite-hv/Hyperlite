import { useCallback, useEffect, useState } from "react";
import { fetchCertificate, importCertificate, requestAcmeCertificate, restorePreviousCertificate, selfSignedCertificate } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState } from "./States";
import { Card, Chip, Field, Loading } from "./ui";

const DOMAIN = /^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$/;
const EMAIL = /^[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}$/;

// The HTTPS certificate of this node's web interface: what it is, and how to replace it (import a PEM pair, get one
// from Let's Encrypt, go back to the previous one or to a self-signed one). The service restarts to take a new
// certificate: the page says so, and the browser may ask to accept the new one.
export default function CertificateCard() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState(null);
  const [pem, setPem] = useState({ certificat: "", cle: "", chaine: "" });
  const [acme, setAcme] = useState({ domaine: "", email: "", test: false });
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setInfo(await fetchCertificate()); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function run(fn, ok, fail) {
    setBusy(true);
    try {
      setInfo(await fn());
      pushToast({ kind: "success", title: ok, message: t("cert.restarting") });
      setMode(null); setPem({ certificat: "", cle: "", chaine: "" });
    } catch (e) { pushToast({ kind: "error", title: fail, message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function confirmThen(title, message, fn, ok, fail) {
    if (await confirmAction({ title, message, confirmLabel: t("cert.apply") })) run(fn, ok, fail);
  }
  if (error && !info) return <Card title={t("cert.title")}><ErrorState message={error} onRetry={load} /></Card>;
  if (!info) return <Card title={t("cert.title")}><Loading /></Card>;

  const source = info.source?.startsWith("acme:") ? "acme" : info.source || "auto";
  const soon = info.present && info.jours_restants < 21;
  const acmeBad = { domaine: acme.domaine !== "" && !DOMAIN.test(acme.domaine), email: acme.email !== "" && !EMAIL.test(acme.email) };
  return (
    <Card title={t("cert.title")} actions={<>
      <button type="button" className="nx-btn" aria-expanded={mode === "import"} onClick={() => setMode(mode === "import" ? null : "import")}>{t("cert.import")}</button>
      <button type="button" className="nx-btn" aria-expanded={mode === "acme"} onClick={() => setMode(mode === "acme" ? null : "acme")}>{t("cert.acme")}</button>
    </>}>
      {info.present ? (
        <dl className="nx-dl2">
          <dt>{t("cert.subject")}</dt><dd className="nx-mono">{info.sujet}</dd>
          <dt>{t("cert.names")}</dt><dd className="nx-mono">{info.noms.join(", ") || "—"}</dd>
          <dt>{t("cert.issuer")}</dt><dd><span className="nx-mono">{info.emetteur}</span> {info.auto_signe && <Chip tone="warning">{t("cert.selfSigned")}</Chip>}</dd>
          <dt>{t("cert.source")}</dt><dd>{t(`cert.src.${source}`)}{source === "acme" ? ` (${info.source.slice(5)})` : ""}</dd>
          <dt>{t("cert.validUntil")}</dt><dd>{formatDateTime(info.fin, lang)} {soon ? <Chip tone="warning">{t("cert.daysLeft", { n: info.jours_restants })}</Chip> : <span className="nx-muted">{t("cert.daysLeft", { n: info.jours_restants })}</span>}</dd>
          <dt>SHA-256</dt><dd className="nx-mono" style={{ wordBreak: "break-all", fontSize: "var(--fs-12)" }}>{info.empreinte_sha256}</dd>
        </dl>
      ) : <p className="nx-muted" style={{ margin: 0 }}>{t("cert.none")}</p>}

      {mode === "import" && (
        <div className="nx-stack" role="group" aria-label={t("cert.import")} style={{ gap: "var(--space-3)", marginTop: "var(--space-4)" }}>
          <p className="nx-muted" style={{ margin: 0, fontSize: "var(--fs-13)" }}>{t("cert.importHelp")}</p>
          <Field label={t("cert.certPem")}>{(p) => <textarea {...p} className="nx-inp nx-mono nx-notes-input" rows={5} value={pem.certificat} placeholder="-----BEGIN CERTIFICATE-----" onChange={(e) => setPem((x) => ({ ...x, certificat: e.target.value }))} />}</Field>
          <Field label={t("cert.keyPem")}>{(p) => <textarea {...p} className="nx-inp nx-mono nx-notes-input" rows={5} value={pem.cle} placeholder="-----BEGIN PRIVATE KEY-----" autoComplete="off" spellCheck={false} onChange={(e) => setPem((x) => ({ ...x, cle: e.target.value }))} />}</Field>
          <Field label={t("cert.chainPem")}>{(p) => <textarea {...p} className="nx-inp nx-mono nx-notes-input" rows={3} value={pem.chaine} onChange={(e) => setPem((x) => ({ ...x, chaine: e.target.value }))} />}</Field>
          <div><button type="button" className="nx-btn nx-btn--primary" disabled={busy || !pem.certificat.includes("BEGIN CERTIFICATE") || !pem.cle.includes("PRIVATE KEY")}
            onClick={() => confirmThen(t("cert.importTitle"), t("cert.restartMsg"), () => importCertificate(pem), t("cert.imported"), t("cert.importFailed"))}>{t("cert.install")}</button></div>
        </div>
      )}

      {mode === "acme" && (
        <div className="nx-stack" role="group" aria-label={t("cert.acme")} style={{ gap: "var(--space-3)", marginTop: "var(--space-4)" }}>
          <p className="nx-muted" style={{ margin: 0, fontSize: "var(--fs-13)" }}>{t(info.certbot ? "cert.acmeHelp" : "cert.noCertbot")}</p>
          {info.certbot && <>
            <div className="nx-fg">
              <Field label={t("cert.domain")} error={acmeBad.domaine ? t("cert.domainInvalid") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={acme.domaine} placeholder="hv1.example.org" onChange={(e) => setAcme((x) => ({ ...x, domaine: e.target.value.trim() }))} />}</Field>
              <Field label={t("cert.email")} hint={t("cert.emailHint")} error={acmeBad.email ? t("cert.emailInvalid") : null}>{(p) => <input {...p} type="email" className="nx-inp" value={acme.email} onChange={(e) => setAcme((x) => ({ ...x, email: e.target.value.trim() }))} />}</Field>
            </div>
            <label className="nx-check"><input type="checkbox" checked={acme.test} onChange={(e) => setAcme((x) => ({ ...x, test: e.target.checked }))} /> {t("cert.staging")}</label>
            <div><button type="button" className="nx-btn nx-btn--primary" disabled={busy || !acme.domaine || !acme.email || acmeBad.domaine || acmeBad.email}
              onClick={() => confirmThen(t("cert.acmeTitle", { d: acme.domaine }), t("cert.restartMsg"), () => requestAcmeCertificate(acme), t("cert.issued"), t("cert.acmeFailed"))}>{busy ? t("cert.requesting") : t("cert.request")}</button></div>
          </>}
        </div>
      )}

      {(info.precedent || !info.auto_signe) && <div className="nx-fa" style={{ justifyContent: "flex-start" }}>
        {info.precedent && <button type="button" className="nx-btn nx-btn--ghost" disabled={busy} onClick={() => confirmThen(t("cert.previousTitle"), t("cert.restartMsg"), restorePreviousCertificate, t("cert.restored"), t("cert.restoreFailed"))}>{t("cert.previous")}</button>}
        {!info.auto_signe && <button type="button" className="nx-btn nx-btn--ghost" disabled={busy} onClick={() => confirmThen(t("cert.selfTitle"), t("cert.selfMsg"), selfSignedCertificate, t("cert.selfDone"), t("cert.selfFailed"))}>{t("cert.self")}</button>}
      </div>}
    </Card>
  );
}
