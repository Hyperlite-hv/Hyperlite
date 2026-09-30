import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import { fetchApiDocsAccess, openSwagger, setApiDocsAccess } from "../../api/client";
import { useAuthStore } from "../../store/useAuthStore";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";
import { PageHeader, Card, Loading } from "../components/ui";

const ACCESS = ["desactive", "admins", "tous"];

// Administration › API: a button that opens Swagger (/docs) in a new tab, where "Authorize" signs in and the API can
// be called; and, for administrators, who may open it. See app/core/api_docs.py.
export default function ApiPage() {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [opening, setOpening] = useState(false);
  const load = () => fetchApiDocsAccess().then((r) => { setState(r); setError(null); }).catch((e) => setError(errorMessage(e)));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function open() {
    // The tab is opened within the click (a popup blocker lets it through), then sent to the single-use link.
    const tab = window.open("", "_blank");
    setOpening(true);
    try {
      const { url } = await openSwagger();
      if (tab) { tab.opener = null; tab.location.href = url; } else window.location.href = url;
    } catch (e) {
      tab?.close();
      pushToast({ kind: "error", title: t("api.openFailed"), message: errorMessage(e) });
    } finally { setOpening(false); }
  }
  async function change(value) {
    try { setState(await setApiDocsAccess(value)); window.dispatchEvent(new Event("nx:api-docs-access")); pushToast({ kind: "success", title: t("api.accessSaved"), message: t(`api.access.${value}`) }); }
    catch (e) { pushToast({ kind: "error", title: t("api.accessFailed"), message: errorMessage(e) }); }
  }
  return (
    <>
      <PageHeader title={t("tab.api")} help={t("api.intro")} />
      {error && !state ? <ErrorState message={error} onRetry={load} /> : !state ? <Loading /> : (
        <>
          <Card title={t("api.docs")}>
            <p className="nx-muted" style={{ margin: "0 0 var(--space-3)" }}>{t(state.autorise ? "api.openHelp" : state.acces === "desactive" ? "api.closed" : "api.notOpen")}</p>
            {state.autorise && <button type="button" className="nx-btn nx-btn--primary" disabled={opening} onClick={open}><ExternalLink size={15} aria-hidden="true" />{t("api.open")}</button>}
          </Card>
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
        </>
      )}
    </>
  );
}
ApiPage.ownHeader = true;
