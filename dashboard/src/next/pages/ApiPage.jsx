import { useEffect, useRef, useState } from "react";
import { Copy, KeyRound, Trash2 } from "lucide-react";
import { createApiToken, deleteApiToken, fetchApiDocsAccess, fetchApiSchema, fetchApiTokens, setApiDocsAccess } from "../../api/client";
import { useAuthStore } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";
import { PageHeader, Card, Field, Loading, TableWrap } from "../components/ui";

const ACCESS = ["desactive", "admins", "tous"];

// The interactive documentation (Swagger UI, bundled: an appliance often has no Internet access). The schema comes
// from an authenticated endpoint and every "Try it out" call carries this session, so it runs with the reader's
// rights and shows in the audit log like any other call.
function Explorer() {
  const t = useT();
  const node = useRef(null);
  const [error, setError] = useState(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [schema, { default: SwaggerUI }] = await Promise.all([
          fetchApiSchema(),
          import("swagger-ui-dist/swagger-ui-es-bundle.js"),
          import("swagger-ui-dist/swagger-ui.css"),
        ]);
        if (!alive || !node.current) return;
        SwaggerUI({
          domNode: node.current,
          spec: schema,
          deepLinking: false,
          docExpansion: "none",
          defaultModelsExpandDepth: -1,
          filter: true,
          tryItOutEnabled: false,
          requestInterceptor: (req) => {
            const token = useAuthStore.getState().token;
            if (token) req.headers.Authorization = `Bearer ${token}`;
            return req;
          },
        });
        setReady(true);
      } catch (e) { if (alive) setError(errorMessage(e)); }
    })();
    return () => { alive = false; };
  }, []);
  if (error) return <ErrorState message={error} />;
  return (
    <>
      {!ready && <Loading />}
      <p className="nx-hint" style={{ margin: "0 0 var(--space-3)" }}>{t("api.explorerHelp")}</p>
      <div ref={node} className="nx-swagger" />
    </>
  );
}

// This account's API tokens, for scripts and tools (the same endpoints as the account security panel).
function Tokens() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [tokens, setTokens] = useState(null);
  const [name, setName] = useState("");
  const [days, setDays] = useState("90");
  const [fresh, setFresh] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => fetchApiTokens().then((r) => setTokens(Array.isArray(r) ? r : [])).catch((e) => pushToast({ kind: "error", title: t("api.tokensFailed"), message: errorMessage(e) }));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const date = (v) => (v ? new Date(v).toLocaleDateString(lang) : "—");
  const daysOk = days === "" || (Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 3650);

  async function create(e) {
    e.preventDefault();
    if (!name.trim() || !daysOk) return;
    setBusy(true);
    try { setFresh(await createApiToken(name.trim(), days === "" ? null : Number(days))); setName(""); load(); }
    catch (er) { pushToast({ kind: "error", title: t("api.tokenCreateFailed"), message: errorMessage(er) }); }
    finally { setBusy(false); }
  }
  async function revoke(tok) {
    if (!(await confirmAction({ title: t("api.revokeTitle", { name: tok.name }), message: t("api.revokeMsg"), confirmLabel: t("api.revoke"), danger: true }))) return;
    try { await deleteApiToken(tok.id); pushToast({ kind: "success", title: t("api.revoked"), message: tok.name }); load(); }
    catch (er) { pushToast({ kind: "error", title: t("api.revokeFailed"), message: errorMessage(er) }); }
  }
  return (
    <Card title={t("api.tokens")} note={tokens?.length || null}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("api.tokensHelp")}</p>
      {fresh && (
        <div className="nx-bn" data-tone="warning" role="status">
          <span className="nx-bn-t"><b>{t("api.freshTitle", { name: fresh.name })}</b> {t("api.freshHelp")}<br /><code className="nx-mono" style={{ userSelect: "all", overflowWrap: "anywhere" }}>{fresh.token}</code></span>
          <button type="button" className="nx-btn nx-btn--sm" onClick={() => navigator.clipboard?.writeText(fresh.token)}><Copy size={14} aria-hidden="true" />{t("api.copy")}</button>
        </div>
      )}
      <form className="nx-fg nx-fg--2" onSubmit={create} style={{ alignItems: "end" }}>
        <Field label={t("api.tokenName")}>{(p) => <input {...p} className="nx-inp" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("api.tokenNameEx")} />}</Field>
        <Field label={t("api.tokenDays")} hint={t("api.tokenDaysHelp")} error={daysOk ? null : t("api.tokenDaysRule")}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={1} max={3650} value={days} onChange={(e) => setDays(e.target.value)} />}</Field>
        <div><button type="submit" className="nx-btn nx-btn--primary" disabled={busy || !name.trim() || !daysOk}><KeyRound size={15} aria-hidden="true" />{t("api.create")}</button></div>
      </form>
      {tokens == null ? <Loading /> : tokens.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) 0 0" }}>{t("api.noTokens")}</p> : (
        <TableWrap>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("api.tokenName")}</th><th scope="col">{t("api.created")}</th><th scope="col">{t("api.lastUsed")}</th><th scope="col">{t("api.expires")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {tokens.map((tok) => (
                <tr key={tok.id}>
                  <th scope="row" style={{ fontWeight: 500 }}>{tok.name}</th>
                  <td>{date(tok.created_at)}</td><td>{date(tok.last_used_at)}</td><td>{tok.expires_at ? date(tok.expires_at) : t("api.never")}</td>
                  <td><div className="nx-ra"><button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("api.revokeX", { name: tok.name })} onClick={() => revoke(tok)}><Trash2 size={15} aria-hidden="true" /></button></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}

// Administration › API: who may read the documentation, the documentation itself, and this account's tokens.
export default function ApiPage() {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const load = () => fetchApiDocsAccess().then((r) => { setState(r); setError(null); }).catch((e) => setError(errorMessage(e)));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  async function change(value) {
    try { setState(await setApiDocsAccess(value)); window.dispatchEvent(new Event("nx:api-docs-access")); pushToast({ kind: "success", title: t("api.accessSaved"), message: t(`api.access.${value}`) }); }
    catch (e) { pushToast({ kind: "error", title: t("api.accessFailed"), message: errorMessage(e) }); }
  }
  return (
    <>
      <PageHeader title={t("tab.api")} help={t("api.intro")} />
      {error && !state ? <ErrorState message={error} onRetry={load} /> : !state ? <Loading /> : (
        <>
          {caps.admin && (
            <Card title={t("api.accessTitle")}>
              <div className="nx-f">
                <span className="nx-f-label" id="api-access">{t("api.accessLabel")}</span>
                <div className="nx-seg2" role="group" aria-labelledby="api-access">
                  {ACCESS.map((v) => <button key={v} type="button" aria-pressed={state.acces === v} onClick={() => state.acces !== v && change(v)}>{t(`api.access.${v}`)}</button>)}
                </div>
                <span className="nx-f-h">{t("api.accessHelp")}</span>
              </div>
            </Card>
          )}
          <Card title={t("api.docs")}>
            {state.autorise ? <Explorer /> : <p className="nx-muted" style={{ margin: 0 }}>{t(state.acces === "desactive" ? "api.closed" : "api.notOpen")}</p>}
          </Card>
          <Tokens />
        </>
      )}
    </>
  );
}
ApiPage.ownHeader = true;
