import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Activity, Monitor, Server } from "lucide-react";
import { fetchTasks } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { capabilities } from "../lib/capabilities";
import { leaveMaintenance } from "../components/MaintenanceDialog";
import { useT, useLangStore } from "../i18n";
import { vmKey } from "../lib/vmId";
import { usePolling } from "../lib/polling";
import { deriveAlerts } from "../lib/alerts";
import { taskLabel } from "../lib/enums";
import { useFreshness } from "../lib/inventory";
import { formatSizeMb, formatSizeGb, formatUptimeLong, formatVersionInt, clockTime, formatDateTime } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import NotesCard from "../components/NotesCard";
import { Essentials, PageTabs, PropGrid, PropSection } from "../components/portal";
import { NodePerformancePage } from "./VmPerformance";

const asList = (v) => (Array.isArray(v) ? v : []);
const pct = (used, total) => (used != null && total ? Math.round((used / total) * 100) : null);

// Overview of a node, as a resource page of a portal: its essentials, then Properties (the machines it runs, its
// system, its recent activity) and Monitoring (the charts of its Performance page).
export default function NodeSummary({ resource: node }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { vms, storagePools, navigateTo } = useInfraStore(useShallow((s) => ({ vms: s.vms, storagePools: s.storagePools, navigateTo: s.navigateTo })));
  const cluster = useFreshness((s) => s.cluster);
  const pushToast = useInfraStore((s) => s.pushToast);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const admin = capabilities(useAuthStore((s) => s.role)).admin;
  const [tab, setTab] = useState("props");
  const [recent, setRecent] = useState(null);
  const nodeId = node?.id;
  const loadRecent = () => fetchTasks({ node: nodeId, limit: 6, sort: "created_at", position: "desc" }).then((r) => setRecent(asList(r))).catch(() => setRecent([]));
  useEffect(() => { loadRecent(); }, [nodeId]); // eslint-disable-line react-hooks/exhaustive-deps
  usePolling(loadRecent, 10000);

  const nodeVms = useMemo(() => vms.filter((v) => v.node === nodeId), [vms, nodeId]);
  if (!node) return null;
  const pools = storagePools.filter((p) => p.node === nodeId);
  const running = nodeVms.filter((v) => v.state === "active").length;
  const alerts = deriveAlerts({ nodes: [node], vms: nodeVms, storagePools: pools, tasks: [] });
  const qemu = formatVersionInt(node.hypervisor_version);
  const lv = formatVersionInt(node.libvirt_version);
  const na = <span className="nx-muted">{t("ns.notReported")}</span>;
  const today = new Date().toDateString();
  const lnk = (label, onClick) => <button type="button" className="nx-lnk" onClick={onClick}>{label}</button>;
  const memP = pct(node.used_memory_mb, node.memoire_totale_mo);
  const stoP = pct(node.stockage_utilise_go, node.stockage_total_go);

  return (
    <>
      {node.maintenance && (
        <div className="nx-bn" data-tone="warning" role="status">
          <span className="nx-bn-t">{t("mt.banner", { by: node.maintenance.started_by, at: formatDateTime(node.maintenance.started_at, lang) })}</span>
          {admin && <button type="button" className="nx-btn nx-btn--sm" onClick={() => leaveMaintenance(node, t, pushToast, refreshAll)}>{t("mt.leave")}</button>}
        </div>
      )}

      <Essentials json={node} storageKey="hyperlite-console-essentials-node"
        left={[
          [t("ns.col.state"), <StatusIndicator key="state" kind="node" wire={node.state} />],
          [t("prop.role"), node.id === "local" ? t("node.roleLocal") : t("node.roleMember")],
          [t("prop.cluster"), cluster?.in_cluster ? cluster.name : t("crumb.datacenter")],
          [t("ns.address"), node.ip || na],
          [t("prop.uptime"), node.uptime_s ? formatUptimeLong(node.uptime_s, lang) : "—"],
        ]}
        right={[
          [t("ns.cpuModel"), <>{node.cpu_modele || na}{node.cpu_coeurs ? <span className="nx-muted"> · {t("nd.cores", { n: node.cpu_coeurs })}</span> : null}</>],
          [t("ns.memory"), node.memoire_totale_mo ? `${formatSizeMb(node.used_memory_mb || 0, lang)} / ${formatSizeMb(node.memoire_totale_mo, lang)}${memP != null ? ` (${memP} %)` : ""}` : na],
          [t("ns.storage"), node.stockage_total_go ? `${formatSizeGb(node.stockage_utilise_go || 0, lang)} / ${formatSizeGb(node.stockage_total_go, lang)}${stoP != null ? ` (${stoP} %)` : ""}` : na],
          [t("nav.vms"), lnk(t("prop.vmsRunning", { n: nodeVms.length, r: running }), () => navigateTo("datacenter", null, "vms"))],
          [t("dock.alerts"), alerts.length === 0 ? t("ns.noAlert") : lnk(t("ns.alertsN", { n: alerts.length }), () => navigateTo("datacenter", null, "summary"))],
        ]}
      />

      <PageTabs label={node.name} value={tab} onChange={setTab} tabs={[["props", t("prop.tab.props")], ["monitor", t("prop.tab.monitor")]]} />

      {tab === "monitor" ? <div className="hc-tabpanel"><NodePerformancePage resource={node} /></div> : (
        <>
          <PropGrid>
            <PropSection icon={Server} title={t("prop.system")} rows={[
              [t("nn.os"), node.os || na],
              [t("nn.kernel"), node.noyau ? <span className="nx-mono">{node.noyau}</span> : na],
              [t("ns.hypervisor"), qemu || lv ? [qemu && `QEMU ${qemu}`, lv && `libvirt ${lv}`].filter(Boolean).join(" · ") : na],
              [t("ns.cpu"), node.cpu_utilisation != null ? `${Math.round(node.cpu_utilisation)} %` : na],
            ]} />
            <PropSection icon={Activity} title={t("ns.activity")} action={lnk(t("prop.seeAll"), () => navigateTo("node", nodeId, "tasks"))}>
              {recent == null ? <p className="nx-muted">…</p> : recent.length === 0 ? <p className="nx-muted" role="status">{t("dock.none")}</p> : (
                <dl className="hc-kv">
                  {recent.map((r) => {
                    const at = r.started_at || r.created_at;
                    return (
                      <div key={r.id}><dt>{new Date(at).toDateString() === today ? clockTime(at, lang) : formatDateTime(at, lang)}</dt>
                        <dd><StatusIndicator kind="task" wire={r.status} compact /> {taskLabel(r.type)}{r.target ? <> · {r.target}</> : null}<span className="nx-muted"> · {r.username || t("ov.system")}</span></dd></div>
                    );
                  })}
                </dl>
              )}
            </PropSection>
          </PropGrid>

          <section className="hc-prop hc-prop--wide">
            <h2><Monitor size={16} aria-hidden="true" />{t("ns.vmsOnNode")} <span className="nx-muted">({nodeVms.length})</span>{lnk(t("ns.allVms"), () => navigateTo("datacenter", null, "vms"))}</h2>
            {nodeVms.length === 0 ? <p className="nx-muted" role="status">{t("ns.noVms")}</p> : (
              <div className="hc-gridwrap">
                <table className="hc-grid hc-grid--list">
                  <thead><tr><th scope="col">{t("ns.col.name")}</th><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("vmlist.col.os")}</th><th scope="col">{t("vmlist.col.res")}</th><th scope="col">{t("vmlist.ip")}</th></tr></thead>
                  <tbody>
                    {nodeVms.map((v) => (
                      <tr key={v.name}>
                        <th scope="row" className="hc-namecell"><div><Monitor size={16} aria-hidden="true" /><span><button type="button" className="nx-lnk" onClick={() => navigateTo("vm", vmKey(v), "summary")}>{v.name}</button></span></div></th>
                        <td><StatusIndicator kind="vm" wire={v.state} /></td>
                        <td>{v.os || <span className="nx-muted">—</span>}</td>
                        <td>{t("vmlist.size", { vcpu: v.vcpu, mem: formatSizeMb(v.memory_mb, lang) })}</td>
                        <td className="hc-num">{v.ip || <span className="nx-muted">—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <div className="hc-notes"><NotesCard kind="node" name={nodeId} canEdit={admin} /></div>
        </>
      )}
    </>
  );
}
