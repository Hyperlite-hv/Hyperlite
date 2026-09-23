import LoadingState from "../../components/LoadingState";
import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Server, MonitorPlay, Square, AlertTriangle, Cpu, MemoryStick, Network, Clock, Plus, ChevronRight } from "lucide-react";
import StatTile from "../../components/StatTile";
import StatusBadge from "../../components/StatusBadge";
import UsageBar from "../../components/UsageBar";
import MetricChart from "../../components/MetricChart";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchHostMetricsHistory, fetchTasks } from "../../api/client";
import { formatMo, formatUptime, formatKbps } from "../../utils/format";
import { statusColor, chartColors } from "../../theme/colors";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";

function sumDefined(items, key) {
  const defined = items.filter((i) => i[key] != null);
  if (!defined.length) return null;
  return defined.reduce((a, i) => a + i[key], 0);
}

// Real states of a libvirt domain (STATE_NAMES, app/routers/vms.py) in the wanted
// display order. "bloque"/"en_arret"/"inconnu" are grouped under "Other" further
// down (transient or rare, not worth a dedicated row as long as no VM is in them).
const VM_STATUS_ORDER = [
  { etat: "actif", label: "Running" },
  { etat: "arrete", label: "Stopped" },
  { etat: "en_pause", label: "Paused" },
  { etat: "suspendu", label: "Suspended" },
  { etat: "plante", label: "In error" },
];

const TASK_LABELS = {
  create_vm: "Create VM", delete_vm: "Delete VM", start_vm: "Start VM",
  stop_vm: "Stop VM", restart_vm: "Restart VM", clone_vm: "Clone VM", migrate_vm: "Migrate VM",
  auto_install: "Unattended installation", create_snapshot: "Create snapshot",
  backup_vm: "Back up VM", restore_backup: "Restore backup",
  export_vm: "Export VM", upload_vm_disk: "Upload disk",
  create_container: "Create container", upload_iso: "Upload ISO",
  hyperlite_update: "Hyperlite update", run_job: "Automation job",
};
const STATUT_ETAT = { en_cours: "avertissement", termine: "actif", echec: "erreur", en_attente: "avertissement" };
const STATUT_LABEL = { en_cours: "Running", termine: "OK", echec: "Failed", en_attente: "Pending" };

// The same alert threshold as the backend (app/core/metrics.py::ALERT_THRESHOLDS),
// computed here on the client from the same data already loaded (there is no
// dedicated /alerts endpoint today, the backend only logs the threshold crossing
// in the audit). It stays honest: only nodes for which there is a real measurement
// (see enrichedNodes) count.
const ALERT_THRESHOLD = 0.9;

function formatHeure(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Real CPU/RAM/network of the local node ("local") through
// GET /host/metrics/history (continuous collection). fetchNodes() (api/client.js)
// deliberately sets these fields to null (not exposed by GET /dashboard), which
// displayed "n/a" permanently here. It is the same source as NodeSummaryTab.jsx.
// Only one real node is measured today (remote nodes do not expose their own
// metrics yet): remote nodes without data stay "n/a" and simply do not count in the
// average/sum (sumDefined already filters out the nulls).
export default function DatacenterSummaryTab() {
  const { nodes, vms, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, navigateTo: s.navigateTo })));
  const [metricRows, setMetricRows] = useState(null);
  const [recentTasks, setRecentTasks] = useState(null);

  useEffect(() => {
    const load = () => fetchHostMetricsHistory("1h").then(setMetricRows).catch(() => {});
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const load = () => fetchTasks({ limit: 6 }).then(setRecentTasks).catch(() => {});
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  const latest = metricRows && metricRows.length ? metricRows[metricRows.length - 1] : null;
  const chartData = (metricRows || []).map((r) => ({
    t: new Date(r.ts).getTime(),
    cpu: (r.cpu_pct ?? 0) / 100,
    ramMo: r.mem_used_mb,
    ramTotalMo: r.mem_total_mb,
    netIn: (r.net_rx_bps ?? 0) / 1024,
    netOut: (r.net_tx_bps ?? 0) / 1024,
  }));

  // Merge the real local metrics into the node list (the local node is always id
  // "local", see fetchNodes()) before any computation: a single source of truth for
  // the aggregate AND the per-node detail below.
  const enrichedNodes = nodes.map((n) => (n.id !== "local" || !latest ? n : {
    ...n,
    cpu_utilisation: latest.cpu_pct != null ? latest.cpu_pct / 100 : n.cpu_utilisation,
    memoire_totale_mo: latest.mem_total_mb ?? n.memoire_totale_mo,
    memoire_utilisee_mo: latest.mem_used_mb ?? n.memoire_utilisee_mo,
  }));

  const totalRamMo = sumDefined(enrichedNodes, "memoire_totale_mo");
  const usedRamMo = sumDefined(enrichedNodes, "memoire_utilisee_mo");
  const cpuNodes = enrichedNodes.filter((n) => n.cpu_utilisation != null);
  const avgCpu = cpuNodes.length ? cpuNodes.reduce((a, n) => a + n.cpu_utilisation, 0) / cpuNodes.length : null;

  const totalVms = vms.length;
  const vmCounts = VM_STATUS_ORDER.map(({ etat, label }) => ({ etat, label, count: vms.filter((v) => v.etat === etat).length }));
  const autreCount = totalVms - vmCounts.reduce((a, c) => a + c.count, 0);
  const runningVms = vmCounts.find((c) => c.etat === "actif")?.count ?? 0;
  const stoppedVms = vmCounts.find((c) => c.etat === "arrete")?.count ?? 0;

  const onlineNodes = enrichedNodes.filter((n) => n.etat === "online").length;
  const alertingNodes = enrichedNodes.filter((n) => {
    const cpuAlert = n.cpu_utilisation != null && n.cpu_utilisation >= ALERT_THRESHOLD;
    const ramAlert = n.memoire_totale_mo && n.memoire_utilisee_mo != null && n.memoire_utilisee_mo / n.memoire_totale_mo >= ALERT_THRESHOLD;
    return cpuAlert || ramAlert;
  });
  const alertCount = alertingNodes.length;

  // Everything shown on this page should lead somewhere: the first matching
  // resource for a given tile/row, or undefined (no link) when nothing matches.
  const firstVmByEtat = (etat) => vms.find((v) => v.etat === etat);
  const goToVm = (v) => v && (() => navigateTo("vm", v.nom, "summary"));
  const goToNode = (n) => n && (() => navigateTo("node", n.id, "summary"));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile icon={Server} label="Nodes" value={enrichedNodes.length} foot={`${onlineNodes} online`} tone="blue" onClick={enrichedNodes.length ? () => navigateTo("datacenter", null, "nodes") : undefined} />
        <StatTile icon={MonitorPlay} label="Running VMs" value={runningVms} foot={`of ${totalVms} in total`} tone="green" onClick={goToVm(firstVmByEtat("actif"))} />
        <StatTile icon={Square} label="Stopped VMs" value={stoppedVms} foot={`of ${totalVms} in total`} tone="gray" onClick={goToVm(firstVmByEtat("arrete"))} />
        <StatTile icon={AlertTriangle} label="Alerts" value={alertCount} foot={alertCount ? "to watch" : "none"} tone="amber" onClick={goToNode(alertingNodes[0])} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="p-4">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-bold text-foreground">
            <Cpu size={16} className="text-accent-blue" /> CPU usage
          </div>
          <div className="mb-1 flex items-baseline gap-2">
            <span className="font-mono text-[22px] font-extrabold text-accent-blue">
              {avgCpu != null ? `${Math.round(avgCpu * 100)}%` : "n/a"}
            </span>
            <span className="text-[11px] text-muted-foreground">{cpuNodes.length} node(s) measured</span>
          </div>
          {chartData.length > 0 ? (
            <MetricChart data={chartData} series={[{ key: "cpu", label: "CPU", color: chartColors.cpu }]} yFormatter={(v) => `${Math.round(v * 100)}%`} height={140} />
          ) : (
            <div className="flex h-[140px] items-center justify-center text-xs text-muted-foreground">No history yet.</div>
          )}
        </Card>

        <Card className="p-4">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-bold text-foreground">
            <MemoryStick size={16} className="text-accent-blue" /> Memory usage
          </div>
          <div className="mb-1 flex items-baseline gap-2">
            <span className="font-mono text-[22px] font-extrabold text-accent-blue">
              {totalRamMo != null && usedRamMo != null ? `${Math.round((usedRamMo / totalRamMo) * 100)}%` : "n/a"}
            </span>
            <span className="text-[11px] text-muted-foreground">
              {totalRamMo != null && usedRamMo != null ? `${formatMo(usedRamMo)} / ${formatMo(totalRamMo)}` : ""}
            </span>
          </div>
          {chartData.length > 0 ? (
            <MetricChart data={chartData} series={[{ key: "ramMo", label: "RAM", color: chartColors.cpu }]} yFormatter={(v) => formatMo(v)} height={140} />
          ) : (
            <div className="flex h-[140px] items-center justify-center text-xs text-muted-foreground">No history yet.</div>
          )}
        </Card>

        <Card className="p-4">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-bold text-foreground">
            <Network size={16} className="text-accent-blue" /> Network (local host)
          </div>
          <div className="mb-1 flex items-baseline gap-2">
            <span className="font-mono text-[22px] font-extrabold text-accent-blue">
              {latest ? formatKbps((latest.net_rx_bps ?? 0) / 1024) : "n/a"}
            </span>
            <span className="text-[11px] text-muted-foreground">current incoming</span>
          </div>
          {chartData.length > 0 ? (
            <MetricChart
              data={chartData}
              series={[{ key: "netIn", label: "Incoming", color: chartColors.cpu }, { key: "netOut", label: "Outgoing", color: chartColors.netOut }]}
              yFormatter={(v) => formatKbps(v)} height={140}
            />
          ) : (
            <div className="flex h-[140px] items-center justify-center text-xs text-muted-foreground">No history yet.</div>
          )}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
        <Card className="p-4 xl:col-span-7">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-[13px] font-bold text-foreground">
              <Server size={16} className="text-accent-blue" /> Nodes
            </h3>
            <Button onClick={() => navigateTo("datacenter", null, "nodes")}>
              <Plus /> Add a node
            </Button>
          </div>
          <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Nodes table">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>CPU</TableHead>
                  <TableHead>Memory</TableHead>
                  <TableHead>Disk</TableHead>
                  <TableHead className="text-right">VMs</TableHead>
                  <TableHead className="text-right">Uptime</TableHead>
                  <TableHead className="w-6" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {enrichedNodes.map((n) => {
                  const ramPct = n.memoire_totale_mo ? (n.memoire_utilisee_mo / n.memoire_totale_mo) * 100 : null;
                  const diskPct = n.stockage_total_go ? (n.stockage_utilise_go / n.stockage_total_go) * 100 : null;
                  const vmTotal = (n.vms_actives ?? 0) + (n.vms_arretees ?? 0);
                  return (
                    <TableRow key={n.id} className="group cursor-pointer" onClick={() => navigateTo("node", n.id, "summary")}>
                      <TableCell>
                        <div className="flex items-center gap-2.5">
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-blue/10 text-accent-blue"><Server size={15} /></span>
                          <div className="min-w-0">
                            <div className="truncate font-semibold text-foreground">{n.nom}</div>
                            <div className="truncate font-mono text-[10.5px] text-muted-foreground">{n.ip || "--"}</div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell><StatusBadge etat={n.etat} /></TableCell>
                      <TableCell><UsageBar pct={n.cpu_utilisation != null ? n.cpu_utilisation * 100 : null} color={chartColors.cpu} /></TableCell>
                      <TableCell><UsageBar pct={ramPct} color={statusColor("actif")} /></TableCell>
                      <TableCell><UsageBar pct={diskPct} color={statusColor("avertissement")} /></TableCell>
                      <TableCell className="text-right font-mono text-xs text-muted-foreground">{n.vms_actives ?? 0} / {vmTotal}</TableCell>
                      <TableCell className="text-right font-mono text-xs text-muted-foreground">{n.uptime_s ? formatUptime(n.uptime_s) : "n/a"}</TableCell>
                      <TableCell>
                        <ChevronRight size={14} className="text-accent-blue opacity-0 -translate-x-1 transition-[opacity,transform] duration-150 group-hover:opacity-100 group-hover:translate-x-0" />
                      </TableCell>
                    </TableRow>
                  );
                })}
                {enrichedNodes.length === 0 && (
                  <TableRow><TableCell colSpan={8} className="py-4 text-center text-sm text-muted-foreground">No nodes.</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </Card>

        <Card className="p-4 xl:col-span-5">
          <h3 className="mb-3 text-[13px] font-bold text-foreground">VM status</h3>
          <div className="divide-y divide-border">
            {vmCounts.map(({ etat, label, count }) => {
              const onClick = count > 0 ? goToVm(firstVmByEtat(etat)) : undefined;
              return (
                <div
                  key={etat}
                  role={onClick ? "button" : undefined}
                  tabIndex={onClick ? 0 : undefined}
                  onClick={onClick}
                  onKeyDown={onClick ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } } : undefined}
                  className={`group flex items-center gap-3 py-2.5 text-sm -mx-1 px-1 rounded-md transition-colors duration-150 ${onClick ? "cursor-pointer hover:bg-muted/40" : ""}`}
                >
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: statusColor(etat) }} />
                  <span className="flex-1 font-semibold text-foreground/90">{label}</span>
                  <span className="font-mono text-[14px] font-extrabold text-foreground">{count}</span>
                  <span className="w-14 text-right font-mono text-xs text-muted-foreground">
                    {totalVms ? `${((count / totalVms) * 100).toFixed(1)}%` : "--"}
                  </span>
                  <ChevronRight size={14} className={`shrink-0 text-accent-blue transition-[opacity,transform] duration-150 ${onClick ? "opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0" : "opacity-0"}`} />
                </div>
              );
            })}
            {autreCount > 0 && (() => {
              const knownEtats = new Set(VM_STATUS_ORDER.map((s) => s.etat));
              const onClick = goToVm(vms.find((v) => !knownEtats.has(v.etat)));
              return (
                <div
                  role={onClick ? "button" : undefined}
                  tabIndex={onClick ? 0 : undefined}
                  onClick={onClick}
                  onKeyDown={onClick ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } } : undefined}
                  className={`group flex items-center gap-3 py-2.5 text-sm -mx-1 px-1 rounded-md transition-colors duration-150 ${onClick ? "cursor-pointer hover:bg-muted/40" : ""}`}
                >
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-muted-foreground/50" />
                  <span className="flex-1 font-semibold text-foreground/90">Other</span>
                  <span className="font-mono text-[14px] font-extrabold text-foreground">{autreCount}</span>
                  <span className="w-14 text-right font-mono text-xs text-muted-foreground">
                    {totalVms ? `${((autreCount / totalVms) * 100).toFixed(1)}%` : "--"}
                  </span>
                  <ChevronRight size={14} className={`shrink-0 text-accent-blue transition-[opacity,transform] duration-150 ${onClick ? "opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0" : "opacity-0"}`} />
                </div>
              );
            })()}
            {totalVms === 0 && <div className="py-2 text-sm text-muted-foreground">No VMs yet.</div>}
          </div>
          {totalVms > 0 && (
            <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-background">
              {vmCounts.filter((c) => c.count > 0).map(({ etat, count }) => (
                <div key={etat} style={{ width: `${(count / totalVms) * 100}%`, backgroundColor: statusColor(etat) }} />
              ))}
              {autreCount > 0 && <div style={{ width: `${(autreCount / totalVms) * 100}%` }} className="bg-muted-foreground/50" />}
            </div>
          )}
        </Card>
      </div>

      <Card className="p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="flex items-center gap-2 text-[13px] font-bold text-foreground">
            <Clock size={16} className="text-accent-blue" /> Recent tasks
          </h3>
          <Button variant="link" className="h-auto p-0 text-accent-blue" onClick={() => navigateTo("datacenter", null, "activity")}>
            View the whole journal →
          </Button>
        </div>
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Recent tasks table">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Node</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Task</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="w-6" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {recentTasks == null && (
                <TableRow><TableCell colSpan={6} className="py-3 text-sm text-muted-foreground"><LoadingState /></TableCell></TableRow>
              )}
              {recentTasks && recentTasks.length === 0 && (
                <TableRow><TableCell colSpan={6} className="py-3 text-sm text-muted-foreground">No recent activity.</TableCell></TableRow>
              )}
              {recentTasks && recentTasks.map((t) => {
                // Link each row to the most specific resource still around: the VM it
                // targeted if it still exists (a deleted VM's tasks stay plain text,
                // there is nothing left to open), otherwise the node it ran on.
                const targetVm = t.cible && vms.find((v) => v.nom === t.cible);
                const targetNode = enrichedNodes.find((n) => n.id === (t.node || "local"));
                const rowClick = targetVm ? () => navigateTo("vm", targetVm.nom, "summary")
                  : targetNode ? () => navigateTo("node", targetNode.id, "summary")
                  : undefined;
                return (
                <TableRow key={t.id} className={`group ${rowClick ? "cursor-pointer" : "hover:bg-transparent"}`} onClick={rowClick}>
                  <TableCell className="font-mono text-xs text-muted-foreground">{formatHeure(t.cree_le)}</TableCell>
                  <TableCell className="text-foreground/90">{t.node || "local"}</TableCell>
                  <TableCell className="text-foreground/90">{t.username || "--"}</TableCell>
                  <TableCell className="text-foreground">
                    {TASK_LABELS[t.type] || t.type}
                    {t.cible && <span className="text-muted-foreground"> — {t.cible}</span>}
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-bold" style={{
                      backgroundColor: `${statusColor(STATUT_ETAT[t.statut])}1A`, color: statusColor(STATUT_ETAT[t.statut]),
                    }}>
                      <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: statusColor(STATUT_ETAT[t.statut]) }} />
                      {STATUT_LABEL[t.statut] || t.statut}
                    </span>
                  </TableCell>
                  <TableCell>
                    {rowClick && (
                      <ChevronRight size={14} className="text-accent-blue opacity-0 -translate-x-1 transition-[opacity,transform] duration-150 group-hover:opacity-100 group-hover:translate-x-0" />
                    )}
                  </TableCell>
                </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </Card>
    </div>
  );
}
