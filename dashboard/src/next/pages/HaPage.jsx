import { useCallback, useEffect, useState } from "react";
import { fetchHaProtected, disableHa, recoverHa } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import StatusIndicator from "../components/StatusIndicator";
import { EmptyState, ErrorState } from "../components/States";

// High availability: protected VMs with the real status of their node. Recovery is always a manual,
// confirmed action (no fencing: recovering while the original node still runs could corrupt the disk).
export default function HaPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const nodes = useInfraStore((s) => s.nodes);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [target, setTarget] = useState({});
  const [busy, setBusy] = useState(null);

  const reload = useCallback(async () => {
    try { const r = await fetchHaProtected(); setRows(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { reload(); const id = setInterval(reload, 15000); return () => clearInterval(id); }, [reload]);

  async function disable(vm) {
    if (!(await confirmAction({ title: t("ha.disableTitle", { name: vm }), message: t("ha.disableMsg"), confirmLabel: t("ha.disable") }))) return;
    try { await disableHa(vm); pushToast({ kind: "success", title: t("ha.disabled"), message: vm }); reload(); }
    catch (e) { pushToast({ kind: "error", title: t("action.failed", { action: t("ha.disable") }), message: errorMessage(e) }); }
  }
  async function recover(vm) {
    const to = target[vm];
    if (!to) return;
    const toName = nodes.find((n) => n.id === to)?.nom || to;
    if (!(await confirmAction({ title: t("ha.recoverTitle", { vm, node: toName }), message: t("ha.recoverMsg"), confirmLabel: t("ha.recover"), danger: true }))) return;
    setBusy(vm);
    try { await recoverHa(vm, to); pushToast({ kind: "success", title: t("ha.recovered"), message: `${vm} → ${toName}` }); reload(); }
    catch (e) { pushToast({ kind: "error", title: t("ha.recoverFailed"), message: errorMessage(e) }); }
    finally { setBusy(null); }
  }
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString(lang, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : null);

  if (error && rows == null) return <ErrorState message={error} onRetry={reload} />;
  const list = rows || [];

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="ha-list">
        <div className="nx-cardhead">
          <h2 id="ha-list">{t("ha.protected")} <span className="nx-count">{rows ? list.length : "…"}</span></h2>
          <button type="button" className="nx-btn" onClick={reload}>{t("action.refresh")}</button>
        </div>
        <p className="nx-muted" style={{ marginTop: 0, maxWidth: "64ch" }}>{t("ha.intro")}</p>
        {rows == null ? <p className="nx-muted" role="status">{t("loading")}</p> : list.length === 0 ? (
          <EmptyState title={t("ha.none")} help={t("ha.noneHelp")} />
        ) : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">VM</th><th scope="col">{t("ha.node")}</th><th scope="col">{t("ha.sync")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {list.map((r) => {
                  const down = r.statut_noeud === "hors_ligne";
                  const targets = nodes.filter((n) => n.id !== r.node && n.etat === "online");
                  return (
                    <tr key={r.vm_name}>
                      <td><StatusIndicator kind="node" wire={down ? "erreur" : "online"} /></td>
                      <th scope="row" className="nx-mono">{r.vm_name}</th>
                      <td className="nx-mono">{nodes.find((n) => n.id === r.node)?.nom || r.node}</td>
                      <td className="nx-mono">{fmt(r.last_synced_at) || <span className="nx-muted">{t("ha.never")}</span>}</td>
                      <td className="nx-num nx-rowactions">
                        {caps.admin && down && (
                          <>
                            <select className="nx-input nx-input--auto" aria-label={`${t("ha.recoveryNode")} ${r.vm_name}`} value={target[r.vm_name] || ""} onChange={(e) => setTarget({ ...target, [r.vm_name]: e.target.value })}>
                              <option value="">{t("ha.recoverOn")}</option>
                              {targets.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}
                            </select>
                            <button type="button" className="nx-btn nx-btn--danger" disabled={!target[r.vm_name] || busy === r.vm_name} aria-label={`Recover ${r.vm_name}`} onClick={() => recover(r.vm_name)}>{busy === r.vm_name ? "…" : t("ha.recover")}</button>
                          </>
                        )}
                        {caps.admin && <button type="button" className="nx-btn" aria-label={`Disable HA for ${r.vm_name}`} onClick={() => disable(r.vm_name)}>{t("ha.disable")}</button>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
