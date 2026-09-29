import { useCallback, useEffect, useState } from "react";
import { fetchHaProtected, disableHa, recoverHa } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import StatusIndicator from "../components/StatusIndicator";
import { ActionsContextMenu, useContextTarget } from "../components/ContextMenu";
import { ErrorState } from "../components/States";
import { PageHeader, Empty, Loading, TableWrap } from "../components/ui";
import { FencingCard, HaSettingsCard, HaWatchBanner, useHaStatus } from "../components/HaDryRun";
import { Heart, Info, Monitor, RefreshCw } from "lucide-react";

// High availability: protected VMs with the real status of their node. Recovery is always a manual, confirmed
// action; automatic HA runs in dry-run mode: the watcher shows, per VM, what it would have done.
export default function HaPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const nodes = useInfraStore((s) => s.nodes);
  const pools = useInfraStore((s) => s.storagePools);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [target, setTarget] = useState({});
  const [busy, setBusy] = useState(null);
  const ctx = useContextTarget(); // right click on a protected VM: its actions
  const [status, reloadStatus] = useHaStatus();

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

  const online = nodes.filter((n) => n.etat === "online").length;
  const shared = pools.filter((p) => p.type === "netfs").length;
  const list = rows || [];

  return (
    <>
      <PageHeader title={t("tab.ha")} count={rows ? list.length : null} desc={t("ha.desc")}
        actions={<button type="button" className="nx-btn" onClick={reload}><RefreshCw size={15} aria-hidden="true" />{t("action.refresh")}</button>} />
      <div className="nx-bn" data-tone={online >= 2 && shared > 0 ? "success" : "info"} role="status"><Info size={16} aria-hidden="true" />
        <span className="nx-bn-t">{t("ha.prereq", { nodes: online, pools: shared })}</span></div>
      <HaWatchBanner status={status} />
      {error && rows == null ? <ErrorState message={error} onRetry={reload} /> : (
        <div className="nx-card2 nx-card2--flush">
          {rows == null ? <Loading style={{ padding: "var(--space-4)" }} /> : list.length === 0 ? (
            <Empty icon={Heart} title={t("ha.none")} text={t("ha.noneHelp")} action={<button type="button" className="nx-btn" onClick={() => navigateTo("datacenter", null, "vms")}><Monitor size={15} aria-hidden="true" />{t("ha.chooseVm")}</button>} />
          ) : (
            <TableWrap>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">VM</th><th scope="col">{t("ha.node")}</th><th scope="col">{t("ha.sync")}</th><th scope="col">{t("hw.state")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                <tbody>
                  {list.map((r) => {
                    const down = r.statut_noeud === "hors_ligne";
                    const targets = nodes.filter((n) => n.id !== r.node && n.etat === "online");
                    return (
                      <tr key={r.vm_name} className={ctx.is("ha", r.vm_name) ? "is-ctx" : undefined} onContextMenu={ctx.open("ha", r, r.vm_name)}>
                        <td><StatusIndicator kind="node" wire={down ? "erreur" : "online"} /></td>
                        <th scope="row"><button type="button" className="nx-lnk nx-mono" onClick={() => navigateTo("vm", r.vm_name, "summary")}>{r.vm_name}</button></th>
                        <td className="nx-mono">{nodes.find((n) => n.id === r.node)?.nom || r.node}</td>
                        <td className="nx-mono">{fmt(r.last_synced_at) || <span className="nx-muted">{t("ha.never")}</span>}</td>
                        <td className="nx-wrapcell">{r.etat_ha ? <span className="nx-chip" data-tone={{ ok: "success", suspect: "warning", en_panne: "danger", libvirt_injoignable: "warning" }[r.etat_ha]}>{t(`hw.s.${r.etat_ha}`)}</span> : <span className="nx-muted">—</span>}{r.derniere_action && <div className="nx-f-h" title={fmt(r.derniere_action_le) || undefined}>{r.derniere_action}</div>}</td>
                        <td><div className="nx-ra">
                          {caps.admin && down && (
                            <>
                              <select className="nx-sel" aria-label={`${t("ha.recoveryNode")} ${r.vm_name}`} value={target[r.vm_name] || ""} onChange={(e) => setTarget({ ...target, [r.vm_name]: e.target.value })}>
                                <option value="">{t("ha.recoverOn")}</option>
                                {targets.map((n) => <option key={n.id} value={n.id}>{n.nom}</option>)}
                              </select>
                              <button type="button" className="nx-btn nx-btn--danger nx-btn--sm" disabled={!target[r.vm_name] || busy === r.vm_name} aria-label={t("a11y.recover_x", { v: r.vm_name })} onClick={() => recover(r.vm_name)}>{busy === r.vm_name ? "…" : t("ha.recover")}</button>
                            </>
                          )}
                          {caps.admin && <button type="button" className="nx-btn nx-btn--sm" aria-label={t("a11y.disable_ha_for_x", { v: r.vm_name })} onClick={() => disable(r.vm_name)}>{t("ha.disable")}</button>}
                        </div></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>
          )}
        </div>
      )}
      {caps.admin && <HaSettingsCard status={status} onSaved={reloadStatus} />}
      {caps.admin && <FencingCard nodes={nodes} />}
      <ActionsContextMenu ctx={ctx} label={(r) => t("ctx.menuOf", { name: r.vm_name })} entries={(r) => [
        { key: "vm", icon: "open", label: t("ctx.openVm"), run: () => navigateTo("vm", r.vm_name, "summary") },
        "-",
        { key: "disable", icon: "unprotect", label: t("ctx.haDisable"), run: () => disable(r.vm_name), disabled: !caps.admin, reason: t("menu.reason.admin") },
      ]} />
    </>
  );
}
HaPage.ownHeader = true;
