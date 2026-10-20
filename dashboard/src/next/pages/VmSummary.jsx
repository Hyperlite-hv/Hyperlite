import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Archive, Camera, Eraser, Heart, TriangleAlert } from "lucide-react";
import { fetchSnapshots, fetchVMBackups, fetchHaProtected, fetchTasks, fetchVMDisks, fetchVMNetwork, fetchBackupSchedule, fetchVMAutoCleanup } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { isRemoteVm, vmKey } from "../lib/vmId";
import { usePolling } from "../lib/polling";
import { taskLabel } from "../lib/enums";
import { formatSizeMb, formatRate, clockTime, formatDateTime } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import { KpiStrip, Card, Loading } from "../components/ui";
import { useVmHistory } from "./VmPerformance";
import NotesCard from "../components/NotesCard";
import { useAuthStore } from "../../store/useAuthStore";
import { capabilities } from "../lib/capabilities";

const asList = (v) => (Array.isArray(v) ? v : []);
const base = (p) => (p ? String(p).split("/").pop() : "");
const pct = (u, tot) => (u != null && tot ? (u / tot) * 100 : null);

// VM summary: four KPI tiles (last hour, opening Performance), the real configuration, the protection state
// with direct links, and the VM's recent activity.
// Every operation lives in the header (primary, Stop, Actions menu): nothing is repeated here.
export default function VmSummary({ resource: vm }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const lang = useLangStore((s) => s.lang);
  const { nodes, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, navigateTo: s.navigateTo })));
  const hist = useVmHistory(vm, "1h");
  const running = vm?.state === "active";
  const [snaps, setSnaps] = useState(null);
  const [backups, setBackups] = useState(null);
  const [schedule, setSchedule] = useState(undefined);
  const [ha, setHa] = useState(null);
  const [cleanup, setCleanup] = useState(null);
  const [disks, setDisks] = useState(null);
  const [net, setNet] = useState(null);
  const [recent, setRecent] = useState(null);
  const name = vm?.name;
  const node = vm?.node;
  const remote = isRemoteVm(vm);

  useEffect(() => {
    if (!name) return;
    setSnaps(null); setBackups(null); setSchedule(undefined); setHa(null); setDisks(null); setNet(null); setCleanup(null);
    fetchVMDisks(name, node).then((r) => setDisks(asList(r))).catch(() => setDisks(false));
    fetchVMNetwork(name, node).then((r) => setNet(asList(r?.interfaces))).catch(() => setNet(false));
    // Served for the VMs of this host only: for a remote VM they would describe a local VM of the same name.
    if (remote) return;
    fetchSnapshots(name).then((r) => setSnaps(asList(r))).catch(() => setSnaps(false));
    fetchVMBackups(name).then((r) => setBackups(asList(r))).catch(() => setBackups([]));
    fetchBackupSchedule(name).then((r) => setSchedule(r || null)).catch(() => setSchedule(null));
    fetchHaProtected().then((r) => setHa(asList(r).some((x) => x.vm_name === name))).catch(() => setHa(false));
    fetchVMAutoCleanup(name).then(setCleanup).catch(() => setCleanup(null));
  }, [name, node]); // eslint-disable-line react-hooks/exhaustive-deps
  const loadRecent = async () => { if (name) setRecent(asList(await fetchTasks({ target: name, limit: 6, sort: "created_at", position: "desc" }))); };
  usePolling(loadRecent, 10000, { enabled: !!name });
  useEffect(() => { loadRecent().catch(() => setRecent([])); }, [name]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!vm) return null;
  const nodeName = nodes.find((n) => n.id === vm.node)?.name || vm.node;
  const na = <span className="nx-muted">{t("ns.notReported")}</span>;
  const rows = (hist.rows || []).slice(-60);
  const last = rows[rows.length - 1];
  const lastMem = [...rows].reverse().find((r) => r.mem_total_mb && r.mem_used_mb != null);
  const memP = lastMem ? pct(lastMem.mem_used_mb, lastMem.mem_total_mb) : null;
  const rd = last?.disk_read_bps ?? null; const wr = last?.disk_write_bps ?? null;
  const rx = last?.net_rx_bps ?? null; const tx = last?.net_tx_bps ?? null;
  const live = running && last;
  const rate = (v) => formatRate(v, lang) || "—";
  const split = (s) => { const m = String(s).match(/^([\d.,\s\u202f]+)\s(.+)$/); return m ? [m[1].trim(), m[2]] : [s, null]; };
  const [diskV, diskU] = live && rd != null ? split(rate(rd + (wr || 0))) : ["—", null];
  const [netV, netU] = live && rx != null ? split(rate(rx + (tx || 0))) : ["—", null];
  const lastBackup = backups && backups.length ? [...backups].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0] : null;
  const goPerf = () => navigateTo("vm", vmKey(vm), "perf");
  const problem = vm.state === "crashed" || vm.state === "blocked";
  const today = new Date().toDateString();

  return (
    <>
      {problem && (
        <div className="nx-bn" data-tone={vm.state === "crashed" ? "danger" : "warning"} role="status"><TriangleAlert size={16} aria-hidden="true" />
          <span className="nx-bn-t">{t(vm.state === "crashed" ? "vm.crashedHelp" : "vm.blockedHelp")}</span></div>
      )}

      <KpiStrip label={t("ns.resources")} items={[
        { id: "cpu", label: t("ns.cpu"), value: live && last.cpu_pct != null ? Math.round(last.cpu_pct) : "—", unit: live && last.cpu_pct != null ? `% · ${vm.vcpu} vCPU` : `${vm.vcpu} vCPU`, sub: running ? null : t("vm.stoppedShort"), spark: rows.map((r) => r.cpu_pct), onClick: goPerf },
        { id: "mem", label: t("ns.memory"), value: live && lastMem ? formatSizeMb(lastMem.mem_used_mb, lang) : "—", unit: `/ ${formatSizeMb(vm.memory_mb, lang)}`, sub: live && memP != null ? `${Math.round(memP)} %` : running ? t("vm.memNeedsAgent") : t("vm.stoppedShort"), spark: rows.map((r) => pct(r.mem_used_mb, r.mem_total_mb)), onClick: goPerf },
        { id: "disk", label: t("vm.disk"), value: diskV, unit: diskU, sub: live && rd != null ? t("vm.rw", { r: rate(rd), w: rate(wr) }) : running ? t("ns.collecting") : t("vm.stoppedShort"), onClick: goPerf },
        { id: "net", label: t("ns.network"), value: netV, unit: netU, sub: live && rx != null ? t("vm.rxtx", { r: rate(rx), t: rate(tx) }) : running ? t("ns.collecting") : t("vm.stoppedShort"), onClick: goPerf },
      ]} />

      <div className="nx-cols2">
        <Card title={t("vm.config")}>
          <dl className="nx-dl2">
            <dt>{t("vm.os")}</dt><dd>{vm.os || na}</dd>
            <dt>{t("ns.node")}</dt><dd><button type="button" className="nx-lnk nx-mono" style={{ fontWeight: 500 }} onClick={() => navigateTo("node", vm.node, "summary")}>{nodeName}</button></dd>
            <dt>{t("vh.processor")}</dt><dd className="nx-mono">{vm.vcpu} vCPU</dd>
            <dt>{t("ct.memory")}</dt><dd className="nx-mono">{formatSizeMb(vm.memory_mb, lang)}</dd>
            <dt>{t("vh.disks")}</dt>
            <dd>{disks == null ? "…" : disks === false ? na : disks.length === 0 ? <span className="nx-muted">{t("vh.noDisks")}</span> : (
              <ul className="nx-plainlist">{disks.map((d) => <li key={d.target} className="nx-mono">{d.type === "cdrom" ? <>{[d.target, t("vh.cdDrive")].join(" · ")}<span className="nx-muted"> · {d.source ? base(d.source) : t("vh.emptyDrive")}</span></> : <>{[d.target, d.bus?.toUpperCase(), d.size_gb ? `${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(d.size_gb)} ${lang === "fr" ? "Go" : "GB"}` : null].filter(Boolean).join(" · ")}{d.pool ? <span className="nx-muted"> ({d.pool})</span> : d.source ? <span className="nx-muted"> · {base(d.source)}</span> : null}</>}</li>)}</ul>
            )}</dd>
            <dt>{t("ns.network")}</dt>
            <dd>{net == null ? "…" : net === false ? na : net.length === 0 ? <span className="nx-muted">—</span> : (
              <ul className="nx-plainlist">{net.map((i) => <li key={i.mac} className="nx-mono">{i.network || i.type_source || "—"} · {i.mac}</li>)}</ul>
            )}</dd>
            <dt>{t("vmlist.ip")}</dt><dd className="nx-mono">{vm.ip || <span className="nx-muted" title={t("ns.ipHelp")}>{t("vm.ipNone")}</span>}</dd>
            <dt>{t("vm.ssh")}</dt><dd className="nx-mono">{vm.ssh_user ? `${vm.ssh_user}${vm.ip ? `@${vm.ip}` : ""}` : na}</dd>
            <dt>{t("vm.tools")}</dt><dd>{vm.guest_agent ? <span title={t("vm.toolsHelp")}>{t(`vm.tools.${vm.guest_agent}`)}</span> : <span className="nx-muted">{t("vm.toolsStopped")}</span>}</dd>
            <dt>{t("wz.firmware")}</dt><dd>{vm.firmware ? t(`fw.${vm.firmware}`) : na}</dd>
            <dt>{t("vm.storageType")}</dt><dd>{vm.iscsi_storage ? "iSCSI" : vm.zfs_storage ? "ZFS" : "qcow2"}</dd>
            <dt>UUID</dt><dd className="nx-mono nx-muted nx-break">{vm.uuid || na}</dd>
          </dl>
        </Card>
        <Card title={t("vm.protection")}>
          {remote ? <p className="nx-muted" style={{ margin: 0 }}>{t("vm.remoteLocalOnly")}</p> : (
          <ul className="nx-list2">
            <li><Archive size={16} aria-hidden="true" />
              <div className="nx-list2-main">{t("vm.lastBackup")}<div className="nx-list2-sub">{backups == null ? "…" : lastBackup ? `${formatDateTime(lastBackup.created_at, lang)} · ${t(lastBackup.status === "failed" ? "state.failed" : lastBackup.status === "succeeded" || lastBackup.status === "success" ? "state.done" : "state.inprogress")}` : t("vm.noBackup")}{schedule ? ` · ${t(`vb.f.${schedule.frequency}`)} ${schedule.time} UTC` : schedule === null ? ` · ${t("vm.noSchedule")}` : ""}</div></div>
              <button type="button" className="nx-btn nx-btn--sm" onClick={() => navigateTo("vm", vmKey(vm), "backup")}>{schedule ? t("vm.manage") : t("ov.plan")}</button></li>
            <li><Camera size={16} aria-hidden="true" />
              <div className="nx-list2-main">{t("tab.snapshots")}<div className="nx-list2-sub">{snaps == null ? "…" : snaps === false ? t("ns.notReported") : snaps.length ? t("vm.snapsN", { n: snaps.length }) : t("vm.snapsNone")}</div></div>
              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => navigateTo("vm", vmKey(vm), "snapshots")}>{t("dock.view")}</button></li>
            <li><Heart size={16} aria-hidden="true" />
              <div className="nx-list2-main">{t("tab.ha")}<div className="nx-list2-sub">{ha == null ? "…" : ha ? t("vm.haOn") : t("vm.haOff")}</div></div></li>
            <li><Eraser size={16} aria-hidden="true" />
              <div className="nx-list2-main">{t("vm.cleanup")}<div className="nx-list2-sub">{cleanup == null ? "…" : cleanup.active ? t("vm.cleanupOn", { n: cleanup.inactive_days }) : t("vm.cleanupOff")}</div></div></li>
          </ul>
          )}
        </Card>
      </div>

      <NotesCard kind="vm" name={vm.name} node={vm.node} canEdit={caps.admin} />

      <Card title={t("ns.activity")}>
        {recent == null ? <Loading style={{ margin: 0 }} /> : recent.length === 0 ? <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("dock.none")}</p> : (
          <ul className="nx-list2">
            {recent.map((r) => {
              const at = r.started_at || r.created_at;
              return (
                <li key={r.id}><StatusIndicator kind="task" wire={r.status} compact />
                  <div className="nx-list2-main">{taskLabel(r.type)}{r.error ? <span className="nx-muted"> — {r.error}</span> : null}<div className="nx-list2-sub">{r.username || t("ov.system")}</div></div>
                  <span className="nx-list2-t">{new Date(at).toDateString() === today ? clockTime(at, lang) : formatDateTime(at, lang)}</span></li>
              );
            })}
          </ul>
        )}
      </Card>
    </>
  );
}
