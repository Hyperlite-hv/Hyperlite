import { useEffect, useState } from "react";
import { RefreshCw, Copy, Power, PowerOff } from "lucide-react";
import { fetchCfsShadow, seedCfsShadow, turnCfsShadowOff, turnCfsShadowOn } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState } from "../components/States";
import ClusterCard from "../components/ClusterCard";
import { PageHeader, Card, KpiStrip, Loading, TableWrap } from "../components/ui";

const KINDS = ["manquants", "en_trop", "differents"];
const gaps = (d) => d.manquants + d.en_trop + d.differents;

// Kinds of data with differences first, then by name in the reader's language.
function sortedDomains(domains, t) {
  return Object.entries(domains).sort(([a, da], [b, db]) => gaps(db) - gaps(da) || t(`cfs.domainName.${a}`).localeCompare(t(`cfs.domainName.${b}`)));
}

// Administration › Replicated configuration: shadow mode of hyperlite-cfs (app/repositories/cfs/shadow.py). Hyperlite
// copies its writes into the daemon after SQLite saved them; this page shows the copies, the failures and every
// difference between the two, and copies SQLite again on request. SQLite stays the source of truth throughout.
export default function CfsPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => fetchCfsShadow().then((r) => { setState(r); setError(null); }).catch((e) => setError(errorMessage(e)));
  useEffect(() => { load(); }, []);

  async function seed() {
    if (!(await confirmAction({ title: t("cfs.seedTitle"), message: t("cfs.seedMsg"), confirmLabel: t("cfs.seed"), danger: false }))) return;
    setBusy(true);
    try {
      const done = await seedCfsShadow();
      pushToast({ kind: "success", title: t("cfs.seeded"), message: t("cfs.seededMsg", { written: done.ecrits, deleted: done.supprimes }) });
      await load();
    } catch (e) {
      pushToast({ kind: "error", title: t("cfs.seedFailed"), message: errorMessage(e) });
    } finally { setBusy(false); }
  }

  async function turnOn() {
    setBusy(true);
    try {
      const done = await turnCfsShadowOn();
      pushToast({ kind: "success", title: t("cfs.turnedOn"), message: t("cfs.seededMsg", { written: done.ecrits, deleted: done.supprimes }) });
      await load();
    } catch (e) {
      pushToast({ kind: "error", title: t("cfs.turnOnFailed"), message: errorMessage(e) });
      await load();
    } finally { setBusy(false); }
  }

  async function turnOff() {
    if (!(await confirmAction({ title: t("cfs.turnOffTitle"), message: t("cfs.turnOffMsg"), confirmLabel: t("cfs.turnOff"), danger: false }))) return;
    setBusy(true);
    try {
      await turnCfsShadowOff();
      pushToast({ kind: "success", title: t("cfs.turnedOff") });
      await load();
    } catch (e) {
      pushToast({ kind: "error", title: t("cfs.turnOffFailed"), message: errorMessage(e) });
    } finally { setBusy(false); }
  }

  const actions = (
    <>
      <button type="button" className="nx-btn" onClick={load}><RefreshCw size={15} aria-hidden="true" />{t("cfs.refresh")}</button>
      {state?.actif && !state.force && <button type="button" className="nx-btn" disabled={busy} onClick={turnOff}><PowerOff size={15} aria-hidden="true" />{t("cfs.turnOff")}</button>}
      {state?.actif && <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !state.joignable} onClick={seed}><Copy size={15} aria-hidden="true" />{t("cfs.seed")}</button>}
    </>
  );
  return (
    <>
      <PageHeader title={t("tab.cfs")} help={t("cfs.intro")} actions={actions} />
      <ClusterCard />
      {error && !state ? <ErrorState message={error} onRetry={load} /> : !state ? <Loading /> : !state.actif ? (
        <Card title={t("cfs.offTitle")}>
          <p className="nx-muted" style={{ margin: "0 0 var(--space-3)" }}>{t("cfs.offHelp")}</p>
          {state.installe ? (
            <button type="button" className="nx-btn nx-btn--primary" disabled={busy} onClick={turnOn}><Power size={15} aria-hidden="true" />{t(busy ? "cfs.turningOn" : "cfs.turnOn")}</button>
          ) : <div className="nx-bn" data-tone="warning" role="alert">{t("cfs.notInstalled")}</div>}
          <p className="nx-f-h" style={{ margin: "var(--space-3) 0 0" }}>{t("cfs.turnOnHelp")}</p>
        </Card>
      ) : (
        <>
          <KpiStrip label={t("cfs.state")} items={[
            { id: "daemon", label: t("cfs.daemon"), dot: state.joignable ? "success" : "danger", value: state.joignable ? t(`cfs.mode.${state.demon.mode}`) : t("cfs.unreachable"),
              sub: state.joignable ? (state.demon.quorum ? t("cfs.quorate") : t("cfs.readOnly")) : null, subTone: state.joignable && !state.demon.quorum ? "warning" : undefined },
            { id: "gaps", label: t("cfs.gaps"), dot: state.joignable ? (state.ecarts ? "warning" : "success") : undefined, value: state.joignable ? state.ecarts : null },
            { id: "pending", label: t("cfs.pending"), dot: state.en_attente ? "warning" : undefined, value: state.en_attente, sub: t("cfs.pendingSub") },
            { id: "copies", label: t("cfs.copies"), value: state.copies, sub: t("cfs.sinceStart") },
            ...(state.joignable && state.demon.mode === "cluster" ? [{ id: "received", label: t("cfs.received"), value: state.reception?.appliques ?? 0, sub: t("cfs.receivedSub") }] : []),
            { id: "failures", label: t("cfs.failures"), dot: state.echecs ? "warning" : undefined, value: state.echecs, sub: t("cfs.sinceStart") },
          ]} />
          {state.force && <p className="nx-f-h">{t("cfs.forced")}</p>}
          {!state.joignable && <div className="nx-bn" data-tone="danger" role="alert">{state.erreur}</div>}
          {state.reception?.probleme && <div className="nx-bn" data-tone="danger" role="alert">{state.reception.probleme}</div>}
          {state.derniere_erreur && (
            <p className="nx-f-h" role="status">{t("cfs.lastError", { when: formatDateTime(state.derniere_erreur_le, lang), error: state.derniere_erreur })}</p>
          )}
          {state.joignable && (
            <Card title={t("cfs.domains")} flush>
              <TableWrap label={t("cfs.domains")}>
                <table className="nx-table">
                  <thead><tr>
                    <th scope="col">{t("cfs.domain")}</th><th scope="col">{t("cfs.entries")}</th>
                    {KINDS.map((k) => <th key={k} scope="col">{t(`cfs.kind.${k}`)}</th>)}
                  </tr></thead>
                  <tbody>
                    {sortedDomains(state.domaines, t).map(([name, d]) => (
                      <tr key={name}>
                        <th scope="row">{t(`cfs.domainName.${name}`)}{d.illisible && <span className="nx-f-h"> — {t("cfs.unreadable")}</span>}</th>
                        <td>{d.entrees}</td>
                        {KINDS.map((k) => <td key={k}>{d[k]}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
              {Object.entries(state.domaines).filter(([, d]) => KINDS.some((k) => d.exemples[k].length)).map(([name, d]) => (
                <details key={name} style={{ margin: "var(--space-3) var(--space-4)" }}>
                  <summary>{t("cfs.examples", { domain: t(`cfs.domainName.${name}`) })}</summary>
                  {KINDS.filter((k) => d.exemples[k].length).map((k) => (
                    <div key={k}>
                      <h3 className="nx-f-label">{t(`cfs.kind.${k}`)}</h3>
                      <ul className="nx-mono">{d.exemples[k].map((p) => <li key={p}>{p}</li>)}</ul>
                    </div>
                  ))}
                </details>
              ))}
              <p className="nx-f-h" style={{ margin: "0 var(--space-4) var(--space-3)" }}>{t(state.ecarts ? "cfs.gapsHelp" : "cfs.noGap")}</p>
            </Card>
          )}
        </>
      )}
    </>
  );
}
CfsPage.ownHeader = true;
