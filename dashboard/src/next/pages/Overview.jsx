import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Check, ChevronRight, Heart, Info, TriangleAlert, Wrench } from "lucide-react";
import { fetchTasks, fetchBackupSchedules, fetchHaProtected } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { useFreshness } from "../lib/inventory";
import { deriveAlerts } from "../lib/alerts";
import { nodeTone } from "../lib/nodeState";
import { taskLabel } from "../lib/enums";
import { formatSizeGb, formatSizeMb, formatUptimeLong, clockTime, formatDateTime } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import { useContextTarget } from "../components/ContextMenu";
import { NodeContextMenu } from "../components/ObjectActions";
import { Card, Loading } from "../components/ui";
import { PerformanceView, useHostHistory } from "./VmPerformance";

const asList = (v) => (Array.isArray(v) ? v : []);
const pct = (used, total) => (used != null && total ? (used / total) * 100 : null);
const round = (v) => (v == null ? null : Math.round(v));

// One of the four health tiles at the top: an icon in its tone, the figure, and what it means.
function Tile({ tone, icon: I, label, value, foot, onClick }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag type={onClick ? "button" : undefined} className="nx-htile" data-tone={tone} onClick={onClick}>
      <span className="nx-htile-ico" aria-hidden="true"><I size={16} strokeWidth={2} /></span>
      <span className="nx-htile-txt"><span>{label}</span><strong>{value}</strong><small>{foot}</small></span>
    </Tag>
  );
}

// A capacity row: one segment per node for what it uses, the free part, and the hatched reserve that keeps room for
// the VMs of the largest node should it fail (N+1). Shown only with two nodes or more.
function CapacityRow({ label, total, parts, reserve, usedPct, after }) {
  const sum = parts.reduce((a, p) => a + p.value, 0);
  const cap = Math.max(total, sum, 1);
  const res = Math.min(reserve || 0, Math.max(0, cap - sum));
  const free = Math.max(0, cap - sum - res);
  return (
    <div className="nx-caprow">
      <span className="nx-caprow-nm"><b>{label}</b><small>{total ? parts.totalLabel : "—"}</small></span>
      <span className="nx-capbar" role="img" aria-label={`${label}: ${usedPct ?? "—"} %`}>
        {parts.map((p, i) => <span key={p.id} title={`${p.name}: ${p.title}`} style={{ flexGrow: p.value, "--seg": i % 6 }} className="nx-capseg" />)}
        <span style={{ flexGrow: free }} />
        {res > 0 && <span className="nx-capres" style={{ flexGrow: res }} />}
      </span>
      <span className="nx-caprow-v"><b>{usedPct == null ? "—" : `${usedPct} %`}</b> <span className="nx-muted">{after}</span></span>
    </div>
  );
}

// The cluster's summary: health, capacity across the nodes, the nodes, what needs attention and the latest activity.
function Summary() {
  const ctx = useContextTarget();
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { nodes, vms, storagePools, tasks, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, tasks: s.tasks, navigateTo: s.navigateTo })));
  const cluster = useFreshness((s) => s.cluster);
  const [recent, setRecent] = useState(null);
  const [scheduled, setScheduled] = useState(null);
  const [ha, setHa] = useState(null);

  const load = async () => {
    fetchTasks({ limit: 6, sort: "created_at", position: "desc" }).then((r) => setRecent(asList(r))).catch(() => setRecent([]));
    fetchBackupSchedules().then((r) => setScheduled(new Set(asList(r).map((x) => x.vm_name)))).catch(() => setScheduled(null));
    fetchHaProtected().then((r) => setHa(asList(r).length)).catch(() => setHa(null));
  };
  useEffect(() => { load(); }, []);
  usePolling(load, 30000);

  const alerts = useMemo(() => deriveAlerts({ nodes, vms, storagePools, tasks }), [nodes, vms, storagePools, tasks]);
    const unscheduled = scheduled ? vms.filter((v) => !scheduled.has(v.name)).length : 0;
  const inCluster = Boolean(cluster?.in_cluster);
  const members = inCluster ? nodes.filter((n) => n.id === "local" || n.member) : nodes;
  const maint = nodes.filter((n) => n.maintenance);

  // What needs attention: the open alerts, nodes short on memory, then the VMs without a backup schedule.
  const watch = [
    ...alerts.map((a) => ({ id: a.id, tone: a.level === "danger" ? "danger" : "warning", text: a.text, sub: t(`alert.kind.${a.kind}`), go: a.target && (() => navigateTo(a.target.type, a.target.id, a.target.tab)) })),
    ...nodes.filter((n) => pct(n.used_memory_mb, n.memoire_totale_mo) >= 80).map((n) => ({ id: `mem-${n.id}`, tone: "warning", text: t("ov.memHigh", { node: n.name, p: round(pct(n.used_memory_mb, n.memoire_totale_mo)) }), sub: t("ov.memHighSub"), action: t("ov.balance"), go: () => navigateTo("datacenter", null, "drs") })),
    ...(unscheduled > 0 ? [{ id: "backups", tone: "info", text: t("ov.noSchedule", { n: unscheduled }), action: t("ov.plan"), go: () => navigateTo("datacenter", null, "backups") }] : []),
  ];
  const critical = watch.filter((w) => w.tone === "danger").length;
  const warnings = watch.filter((w) => w.tone === "warning").length;

  // Capacity, from each node's live figures.
  const sampled = members.filter((n) => n.cpu_coeurs);
  const cores = sampled.reduce((a, n) => a + n.cpu_coeurs, 0);
  const cpuParts = sampled.map((n) => ({ id: n.id, name: n.name, value: ((n.cpu_utilisation || 0) / 100) * n.cpu_coeurs, title: `${round(n.cpu_utilisation) ?? "—"} %` }));
  const cpuUsed = cpuParts.reduce((a, p) => a + p.value, 0);
  const vcpus = vms.filter((v) => v.state === "active").reduce((a, v) => a + (v.vcpu || 0), 0);
  const memNodes = members.filter((n) => n.memoire_totale_mo);
  const memTotal = memNodes.reduce((a, n) => a + n.memoire_totale_mo, 0);
  const memParts = memNodes.map((n) => ({ id: n.id, name: n.name, value: n.used_memory_mb || 0, title: formatSizeMb(n.used_memory_mb, lang) || "—" }));
  const memUsed = memParts.reduce((a, p) => a + p.value, 0);
  const n1 = memNodes.length >= 2;
  const memReserve = n1 ? Math.max(...memNodes.map((n) => n.memoire_totale_mo)) : 0;
  const cpuReserve = n1 && sampled.length >= 2 ? Math.max(...sampled.map((n) => n.cpu_coeurs)) : 0;
  const stoNodes = members.filter((n) => n.stockage_total_go);
  const stoTotal = stoNodes.reduce((a, n) => a + n.stockage_total_go, 0);
  const stoParts = stoNodes.map((n) => ({ id: n.id, name: n.name, value: n.stockage_utilise_go || 0, title: formatSizeGb(n.stockage_utilise_go, lang) || "—" }));
  const stoUsed = stoParts.reduce((a, p) => a + p.value, 0);
  const afterFailure = memTotal - memUsed - memReserve;
  cpuParts.totalLabel = t("nd.cores", { n: cores });
  memParts.totalLabel = formatSizeMb(memTotal, lang);
  stoParts.totalLabel = formatSizeGb(stoTotal, lang);

  return (
    <>
      <section className="nx-htiles" aria-label={t("ov.health")}>
        {inCluster
          ? <Tile tone={cluster.quorum === false ? "danger" : "success"} icon={cluster.quorum === false ? TriangleAlert : Check} label={t("ov.quorum")}
              value={cluster.quorum === false ? t("ov.quorumLost") : t("ov.votes", { n: asList(cluster.members).reduce((a, m) => a + (m.votes || 1), 0) })}
              foot={t("ov.members", { n: asList(cluster.members).length })} onClick={() => navigateTo("datacenter", null, "cfs")} />
          : <Tile tone="info" icon={Info} label={t("ov.quorum")} value={t("ov.standalone")} foot={t("ov.standaloneSub")} onClick={() => navigateTo("datacenter", null, "cfs")} />}
        <Tile tone={ha ? "success" : "info"} icon={Heart} label={t("nav.ha")} value={ha == null ? "—" : t("ov.haProtected", { n: ha })} foot={ha ? t("ov.haSub") : t("ov.haNone")} onClick={() => navigateTo("datacenter", null, "ha")} />
        <Tile tone={critical ? "danger" : warnings ? "warning" : "success"} icon={critical || warnings ? TriangleAlert : Check} label={t("ov.alerts")}
          value={critical ? t("ov.critical", { n: critical }) : warnings ? t("ov.warnings", { n: warnings }) : t("ov.noAlert")}
          foot={critical && warnings ? t("ov.warnings", { n: warnings }) : t("ov.alertsSub")} />
        <Tile tone={maint.length ? "info" : "success"} icon={Wrench} label={t("ov.maintenance")} value={maint.length ? maint.map((n) => n.name).join(", ") : t("ov.noMaint")}
          foot={maint.length ? t("ov.maintSub") : t("ov.allServing")} onClick={() => navigateTo("datacenter", null, "nodes")} />
      </section>

      <div className="nx-ovgrid">
        <div className="nx-ovmain">
          <Card title={t("ov.capTitle")}>
            {n1 && <p className="nx-cardnote">{t("ov.capacityNote")}</p>}
            <div className="nx-caprows">
              <CapacityRow label={t("ns.cpu")} total={cores} parts={cpuParts} reserve={cpuReserve} usedPct={cores ? round((cpuUsed / cores) * 100) : null} after={t("ov.vcpus", { n: vcpus })} />
              <CapacityRow label={t("ns.memory")} total={memTotal} parts={memParts} reserve={memReserve} usedPct={memTotal ? round((memUsed / memTotal) * 100) : null}
                after={n1 ? (afterFailure >= 0 ? t("ov.freeAfter", { v: formatSizeMb(afterFailure, lang) }) : t("ov.noRoomAfter")) : t("ov.freeV", { v: formatSizeMb(memTotal - memUsed, lang) || "—" })} />
              <CapacityRow label={t("ns.storage")} total={stoTotal} parts={stoParts} reserve={0} usedPct={stoTotal ? round((stoUsed / stoTotal) * 100) : null} after={t("ov.freeV", { v: formatSizeGb(stoTotal - stoUsed, lang) || "—" })} />
            </div>
          </Card>

          <section className="nx-ovnodes" aria-labelledby="ov-nodes">
            <div className="nx-ovnodes-h">
              <h2 id="ov-nodes">{t("nav.nodes")}</h2>
              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => navigateTo("datacenter", null, "nodes")}>{t("ov.fleet")}<ChevronRight size={14} aria-hidden="true" /></button>
            </div>
            <div className="nx-ncards">
              {[...nodes].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })).map((n) => {
                const s = nodeTone(n);
                const nv = vms.filter((v) => (v.node || "local") === n.id);
                const cpu = round(n.cpu_utilisation);
                const mem = round(pct(n.used_memory_mb, n.memoire_totale_mo));
                const note = n.state !== "online" ? t("ov.nodeDown")
                  : n.maintenance ? t("ov.nodeMaint")
                  : s.tone === "warning" ? t(s.key === "tree.state.memory" ? "ov.nodeMem" : "ov.nodeCpu", { p: s.key === "tree.state.memory" ? mem : cpu })
                  : n.uptime_s ? t("node.upSince", { d: formatUptimeLong(n.uptime_s, lang) }) : null;
                return (
                  <article key={n.id} className={`nx-ncard${ctx.is("node", n.id) ? " is-ctx" : ""}`} data-tone={s.tone} onContextMenu={ctx.open("node", n, n.id)}>
                    <div className="nx-ncard-h">
                      <button type="button" className="nx-lnk" onClick={() => navigateTo("node", n.id, "summary")}>{n.name}</button>
                      <span className="nx-chip" data-tone={s.tone}>{t(s.key)}</span>
                      <span className="nx-ncard-vms">{t("ov.vmCount", { n: nv.length })}</span>
                    </div>
                    <div className="nx-ncard-m">
                      <span>CPU</span><span className="nx-mbar"><span data-hot={cpu >= 80 || undefined} style={{ width: `${cpu ?? 0}%` }} /></span><span>{cpu == null ? "—" : `${cpu} %`}</span>
                      <span>RAM</span><span className="nx-mbar"><span data-hot={mem >= 80 || undefined} style={{ width: `${mem ?? 0}%` }} /></span><span>{mem == null ? "—" : `${mem} %`}</span>
                    </div>
                    <small className="nx-ncard-note" data-tone={s.tone === "success" ? undefined : s.tone}>{note || (n.id === "local" ? t("node.roleLocal") : t("node.roleMember"))}</small>
                  </article>
                );
              })}
            </div>
            {ctx.target && <NodeContextMenu key={ctx.target.obj.id} node={ctx.target.obj} at={ctx.target.at} returnFocus={ctx.target.el} onDone={ctx.close} />}
          </section>
        </div>

        <div className="nx-ovside">
          <Card title={t("ov.alerts")} note={watch.length ? [critical && t("ov.critical", { n: critical }), warnings && t("ov.warnings", { n: warnings })].filter(Boolean).join(" · ") || null : null} flush>
            {watch.length === 0 ? <p role="status" className="nx-ovempty"><StatusIndicator override={{ key: "health.ok", shape: "dot", tone: "success" }} /> <span className="nx-muted">{t("ns.noIncident")}</span></p> : (
              <ul className="nx-alist">
                {watch.slice(0, 8).map((w) => (
                  <li key={w.id} data-tone={w.tone}>
                    <span className="nx-alist-t">{w.text}{w.sub && <small>{w.sub}</small>}</span>
                    {w.go && <button type="button" className="nx-lnk nx-alist-go" onClick={w.go}>{w.action || t("dock.view")}</button>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title={t("ns.activity")} actions={<button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => navigateTo("datacenter", null, "activity")}>{t("ov.seeAll")}<ChevronRight size={14} aria-hidden="true" /></button>}>
            <ActivityList items={recent} max={5} />
          </Card>
        </div>
      </div>
    </>
  );
}

function ActivityList({ items, max }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  if (items == null) return <Loading style={{ margin: 0 }} />;
  if (items.length === 0) return <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("dock.none")}</p>;
  const today = new Date().toDateString();
  return (
    <ul className="nx-list2">
      {items.slice(0, max).map((r) => {
        const at = r.started_at || r.created_at;
        return (
          <li key={r.id}>
            <StatusIndicator kind="task" wire={r.status} compact />
            <div className="nx-list2-main">{taskLabel(r.type)}{r.target ? <> · <b>{r.target}</b></> : null}<div className="nx-list2-sub">{r.username || t("ov.system")}</div></div>
            <span className="nx-list2-t">{new Date(at).toDateString() === today ? clockTime(at, lang) : formatDateTime(at, lang)}</span>
          </li>
        );
      })}
    </ul>
  );
}

// The cluster's Performance tab: one node's charts at a time.
export function ClusterPerf() {
  const t = useT();
  const nodes = useInfraStore((s) => s.nodes);
  const [nodeId, setNodeId] = useState("local");
  const [range, setRange] = useState("1h");
  const node = nodes.find((n) => n.id === nodeId) || nodes[0];
  const hist = useHostHistory(node, range);
  if (!node) return null;
  return (
    <div className="nx-stack">
      {nodes.length > 1 && (
        <div className="nx-bar">
          <label className="nx-bar-lbl">{t("ns.node")}<select className="nx-sel" value={node.id} onChange={(e) => setNodeId(e.target.value)}>{nodes.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label>
        </div>
      )}
      <PerformanceView hist={hist} range={range} setRange={setRange} running={node.state === "online"} title={t("ov.hostPerf", { name: node.name })} />
    </div>
  );
}
ClusterPerf.ownHeader = true;

export default function Overview() {
  return <div className="nx-stack"><Summary /></div>;
}
Overview.ownHeader = true;
