import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Activity, Cpu, DatabaseBackup, HardDrive, HeartPulse, Monitor, Network, TriangleAlert } from "lucide-react";
import { fetchSnapshots, fetchVMBackups, fetchHaProtected, fetchTasks, fetchVMDisks, fetchVMNetwork, fetchBackupSchedule, fetchVMAutoCleanup } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { isRemoteVm, vmKey } from "../lib/vmId";
import { usePolling } from "../lib/polling";
import { taskLabel } from "../lib/enums";
import { formatSizeMb, formatUptimeLong, clockTime, formatDateTime } from "../lib/format";
import { useFreshness } from "../lib/inventory";
import StatusIndicator from "../components/StatusIndicator";
import NotesCard, { TagChips } from "../components/NotesCard";
import { useAuthStore } from "../../store/useAuthStore";
import { capabilities } from "../lib/capabilities";
import { metaOf, useMetaStore } from "../lib/meta";
import MigrateDialog from "../components/MigrateDialog";
import { Essentials, PageTabs, PropGrid, PropSection } from "../components/portal";
import VmPerformancePage from "./VmPerformance";

const asList = (v) => (Array.isArray(v) ? v : []);
const base = (p) => (p ? String(p).split("/").pop() : "");

// Overview of a VM, as a resource page of a portal: the essentials on two columns, then two tabs. Properties lists
// the configuration by subject (machine, network, availability, size, disks, protection, activity), each with a
// link to the page that changes it; Monitoring shows the charts of the Performance page.
// Every operation lives in the command bar above: nothing is repeated here.
export default function VmSummary({ resource: vm }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const lang = useLangStore((s) => s.lang);
  const { nodes, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, navigateTo: s.navigateTo })));
  const cluster = useFreshness((s) => s.cluster);
  const { byKey: metaByKey, load: loadMeta } = useMetaStore(useShallow((s) => ({ byKey: s.byKey, load: s.load })));
  useEffect(() => { loadMeta(); }, [loadMeta]);
  const [tab, setTab] = useState("props");
  const [snaps, setSnaps] = useState(null);
  const [backups, setBackups] = useState(null);
  const [schedule, setSchedule] = useState(undefined);
  const [ha, setHa] = useState(null);
  const [cleanup, setCleanup] = useState(null);
  const [disks, setDisks] = useState(null);
  const [net, setNet] = useState(null);
  const [recent, setRecent] = useState(null);
  const [migrating, setMigrating] = useState(false);
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
  const loadRecent = async () => { if (name) setRecent(asList(await fetchTasks({ target: name, limit: 5, sort: "created_at", position: "desc" }))); };
  usePolling(loadRecent, 10000, { enabled: !!name });
  useEffect(() => { loadRecent().catch(() => setRecent([])); }, [name]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!vm) return null;
  const running = vm.state === "active";
  const nodeObj = nodes.find((n) => n.id === vm.node);
  const nodeName = nodeObj?.name || vm.node;
  const canMigrate = caps.admin && running && nodes.length > 1;
  const na = <span className="nx-muted">{t("ns.notReported")}</span>;
  const wait = <span className="nx-muted">…</span>;
  const go = (page) => navigateTo("vm", vmKey(vm), page);
  const edit = (page, label = t("prop.edit")) => (caps.admin && !remote ? <button type="button" className="nx-lnk" onClick={() => go(page)}>{label}</button> : null);
  const lnk = (label, onClick) => <button type="button" className="nx-lnk" onClick={onClick}>{label}</button>;
  const tags = metaOf(metaByKey, "vm", vm.node, vm.name)?.tags || [];
  const lastBackup = backups && backups.length ? [...backups].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0] : null;
  const firstNic = Array.isArray(net) ? net[0] : null;
  const realDisks = Array.isArray(disks) ? disks.filter((d) => d.type !== "cdrom") : [];
  const cdroms = Array.isArray(disks) ? disks.filter((d) => d.type === "cdrom") : [];
  const gb = (v) => `${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(v)} ${lang === "fr" ? "Go" : "GB"}`;
  const problem = vm.state === "crashed" || vm.state === "blocked";
  const today = new Date().toDateString();

  return (
    <>
      {problem && (
        <div className="nx-bn" data-tone={vm.state === "crashed" ? "danger" : "warning"} role="status"><TriangleAlert size={16} aria-hidden="true" />
          <span className="nx-bn-t">{t(vm.state === "crashed" ? "vm.crashedHelp" : "vm.blockedHelp")}</span></div>
      )}

      <Essentials json={vm}
        left={[
          [t("ns.node"), <>{lnk(nodeName, () => navigateTo("node", vm.node, "summary"))}{canMigrate && <> ({lnk(t("prop.migrate"), () => setMigrating(true))})</>}</>],
          [t("ns.col.state"), <StatusIndicator key="state" kind="vm" wire={vm.state} />],
          [t("prop.cluster"), cluster?.in_cluster ? cluster.name : t("crumb.datacenter")],
          ["VMID", vm.vmid != null ? String(vm.vmid) : <span className="nx-muted">{t("vmid.pending")}</span>],
          [t("prop.uptime"), running && vm.uptime_s ? formatUptimeLong(vm.uptime_s, lang) : "—"],
        ]}
        right={[
          [t("vm.os"), vm.os || na],
          [t("prop.size"), t("vmlist.size", { vcpu: vm.vcpu, mem: formatSizeMb(vm.memory_mb, lang) })],
          [t("vmlist.ip"), vm.ip || <span className="nx-muted" title={t("ns.ipHelp")}>{t("vm.ipNone")}</span>],
          [t("prop.network"), net == null ? wait : firstNic ? `${firstNic.network || firstNic.type_source || "—"}${firstNic.vlan != null ? ` / VLAN ${firstNic.vlan}` : ""}` : na],
          [t("vm.tools"), vm.guest_agent ? t(`vm.tools.${vm.guest_agent}`) : <span className="nx-muted">{t("vm.toolsStopped")}</span>],
        ]}
        footer={<div className="hc-kv"><div><dt>{t("vmlist.tags")}</dt><dd>{tags.length ? <TagChips tags={tags} /> : <span className="nx-muted">{t("prop.noTags")}</span>}</dd></div></div>}
      />

      <PageTabs label={vm.name} value={tab} onChange={setTab} tabs={[["props", t("prop.tab.props")], ["monitor", t("prop.tab.monitor")]]} />

      {tab === "monitor" ? <div className="hc-tabpanel"><VmPerformancePage resource={vm} /></div> : (
        <PropGrid>
          <PropSection icon={Monitor} title={t("prop.vm")} rows={[
            [t("ns.col.name"), vm.name],
            [t("vm.os"), vm.os || na],
            [t("wz.firmware"), vm.firmware ? t(`fw.${vm.firmware}`) : na],
            [t("vm.storageType"), vm.iscsi_storage ? "iSCSI" : vm.zfs_storage ? "ZFS" : vm.lvm_storage ? "LVM" : "qcow2"],
            [t("vm.tools"), vm.guest_agent ? t(`vm.tools.${vm.guest_agent}`) : <span className="nx-muted">{t("vm.toolsStopped")}</span>],
            ["UUID", vm.uuid ? <span className="nx-mono nx-break">{vm.uuid}</span> : na],
          ]} />
          <PropSection icon={Network} title={t("prop.networking")} action={edit("network")} rows={[
            [t("vmlist.ip"), vm.ip || <span className="nx-muted">{t("vm.ipNone")}</span>],
            ...(net == null ? [[t("prop.interfaces"), wait]] : net === false ? [[t("prop.interfaces"), na]] : net.map((i, n) => [
              `${t("prop.interface")} ${n + 1}`, <>{i.network || i.type_source || "—"}{i.vlan != null ? ` / VLAN ${i.vlan}` : ""} · {i.model || "—"} · <span className="nx-mono">{i.mac}</span></>,
            ])),
            [t("vm.ssh"), vm.ssh_user ? <span className="nx-mono">{`${vm.ssh_user}${vm.ip ? `@${vm.ip}` : ""}`}</span> : na],
          ]} />
          <PropSection icon={HeartPulse} title={t("prop.availability")} rows={[
            [t("ns.node"), lnk(nodeName, () => navigateTo("node", vm.node, "summary"))],
            [t("tab.ha"), remote ? <span className="nx-muted">{t("vm.remoteLocalOnly")}</span> : ha == null ? wait : ha ? lnk(t("vm.haOn"), () => navigateTo("datacenter", null, "ha")) : <>{t("vm.haOff")} ({lnk(t("vm.protectHa"), () => navigateTo("datacenter", null, "ha"))})</>],
            [t("vm.ifNodeFails", { node: nodeName }), remote ? "—" : ha == null ? wait : ha ? t("vm.restartsElsewhere") : t("vm.staysDown")],
            [t("vm.cleanup"), remote ? null : cleanup == null ? wait : cleanup.active ? t("vm.cleanupOn", { n: cleanup.inactive_days }) : t("vm.cleanupOff")],
          ]} />
          <PropSection icon={Cpu} title={t("prop.size")} action={edit("hardware")} rows={[
            [t("prop.size"), t("vmlist.size", { vcpu: vm.vcpu, mem: formatSizeMb(vm.memory_mb, lang) })],
            [t("vh.processor"), `${vm.vcpu} vCPU`],
            [t("ct.memory"), formatSizeMb(vm.memory_mb, lang)],
          ]} />
          <PropSection icon={HardDrive} title={t("prop.disks")} action={edit("hardware")} rows={
            disks == null ? [[t("prop.disks"), wait]] : disks === false ? [[t("prop.disks"), na]] : [
              ...realDisks.map((d) => [d.target, <>{d.size_gb ? gb(d.size_gb) : "—"}{d.pool ? <> · {lnk(d.pool, () => navigateTo("datacenter", null, "storage"))}</> : null} · {(d.bus || "").toUpperCase() || base(d.source)}</>]),
              ...cdroms.map((d) => [`${t("vh.cdDrive")} ${d.target}`, d.source ? base(d.source) : t("vh.emptyDrive")]),
            ]} />
          <PropSection icon={DatabaseBackup} title={t("prop.protection")} action={remote ? null : <button type="button" className="nx-lnk" onClick={() => go("backup")}>{t("vm.manage")}</button>} rows={remote ? [[t("vm.protection"), <span key="remote" className="nx-muted">{t("vm.remoteLocalOnly")}</span>]] : [
            [t("prop.schedule"), schedule === undefined ? wait : schedule ? t("prop.scheduled") : <>{t("prop.notScheduled")} ({lnk(t("ov.plan"), () => go("backup"))})</>],
            [t("vm.lastBackup"), backups == null ? wait : lastBackup ? `${formatDateTime(lastBackup.created_at, lang)} · ${t(lastBackup.status === "failed" ? "state.failed" : "state.done")}` : t("bk.none")],
            [t("tab.snapshots"), snaps == null ? wait : snaps === false ? na : lnk(snaps.length ? t("vm.snapsN", { n: snaps.length }) : t("vm.snapsNone"), () => go("snapshots"))],
          ]} />
          <PropSection icon={Activity} title={t("ns.activity")} action={<button type="button" className="nx-lnk" onClick={() => go("tasks")}>{t("prop.seeAll")}</button>}>
            {recent == null ? <p className="nx-muted">…</p> : recent.length === 0 ? <p className="nx-muted" role="status">{t("dock.none")}</p> : (
              <dl className="hc-kv">
                {recent.map((r) => {
                  const at = r.started_at || r.created_at;
                  return (
                    <div key={r.id}><dt>{new Date(at).toDateString() === today ? clockTime(at, lang) : formatDateTime(at, lang)}</dt>
                      <dd><StatusIndicator kind="task" wire={r.status} compact /> {taskLabel(r.type)}<span className="nx-muted"> · {r.username || t("ov.system")}</span></dd></div>
                  );
                })}
              </dl>
            )}
          </PropSection>
        </PropGrid>
      )}
      {tab === "props" && <div className="hc-notes"><NotesCard kind="vm" name={vm.name} node={vm.node} canEdit={caps.admin} /></div>}
      {migrating && <MigrateDialog vm={vm} nodes={nodes} onClose={() => setMigrating(false)} />}
    </>
  );
}
