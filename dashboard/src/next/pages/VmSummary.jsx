import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchSnapshots, fetchVMBackups, fetchHaProtected, fetchTasks, fetchVMDisks, fetchVMNetwork, fetchBackupSchedule } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { taskLabel } from "../lib/enums";
import { formatSizeMb, formatUptimeLong, clockTime, formatDateTime } from "../lib/format";
import StatusIndicator from "../components/StatusIndicator";
import { useVmHistory, VmChartGrid } from "./VmPerformance";
import VMSummaryTab from "../../panels/vm/VMSummaryTab";

const asList = (v) => (Array.isArray(v) ? v : []);
const base = (p) => (p ? String(p).split("/").pop() : "");
const SNAP_ROWS = 5;
// libvirt reports snapshot times as Unix seconds; older payloads may carry an ISO string.
const snapTs = (v) => (/^\d+$/.test(String(v ?? "")) ? Number(v) * 1000 : Date.parse(v) || 0);

// VM summary, laid out like the reference console: the configuration on the left, the last hour of the four
// performance charts and the latest snapshots on the right, then the activity of this VM. Every operation of the
// historical screen (clone, template, migrate, HA, auto-clean-up, delete...) stays in "All operations".
export default function VmSummary({ resource: vm, selection }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { nodes, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, navigateTo: s.navigateTo })));
  const hist = useVmHistory(vm, "1h");
  const [snaps, setSnaps] = useState(null);
  const [backups, setBackups] = useState(null);
  const [schedule, setSchedule] = useState(undefined);
  const [ha, setHa] = useState(null);
  const [disks, setDisks] = useState(null);
  const [net, setNet] = useState(null);
  const [recent, setRecent] = useState(null);

  useEffect(() => {
    if (!vm) return;
    setSnaps(null); setBackups(null); setSchedule(undefined); setHa(null); setDisks(null); setNet(null);
    fetchSnapshots(vm.nom).then((r) => setSnaps(asList(r))).catch(() => setSnaps([]));
    fetchVMBackups(vm.nom).then((r) => setBackups(asList(r))).catch(() => setBackups([]));
    fetchBackupSchedule(vm.nom).then((r) => setSchedule(r || null)).catch(() => setSchedule(null));
    fetchHaProtected().then((r) => setHa(asList(r).some((x) => x.vm_name === vm.nom))).catch(() => setHa(false));
    fetchVMDisks(vm.nom).then((r) => setDisks(asList(r))).catch(() => setDisks(false));
    fetchVMNetwork(vm.nom).then((r) => setNet(asList(r?.interfaces))).catch(() => setNet(false));
  }, [vm?.nom]); // eslint-disable-line react-hooks/exhaustive-deps
  const loadRecent = async () => { if (vm) setRecent(asList(await fetchTasks({ cible: vm.nom, limit: 6, tri: "cree_le", ordre: "desc" }))); };
  usePolling(loadRecent, 10000, { enabled: !!vm });
  useEffect(() => { loadRecent().catch(() => setRecent([])); }, [vm?.nom]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!vm) return null;
  const node = nodes.find((n) => n.id === vm.node);
  const na = t("ns.notReported");
  const problem = vm.etat === "plante" || vm.etat === "bloque";
  const lastBackup = backups && backups.length ? [...backups].sort((a, b) => String(b.cree_le).localeCompare(String(a.cree_le)))[0] : null;
  const go = (tab) => navigateTo("vm", vm.nom, tab);
  const snapList = snaps ? [...snaps].sort((a, b) => snapTs(b.date_creation) - snapTs(a.date_creation)) : [];
  const snapKind = (s) => (s.etat_vm === "disque_seul" ? t("vs.zfsDisk") : s.etat_vm === "running" ? t("vs.withMemory") : s.etat_vm ? t("vs.diskOnly") : "—");
  const loadingDd = <span className="nx-muted">…</span>;

  return (
    <div className="nx-ns">
      {problem && (
        <div className={`nx-banner ${vm.etat === "plante" ? "nx-banner--danger" : ""}`} role="status" style={{ borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-default)" }}>
          <StatusIndicator kind="vm" wire={vm.etat} />
          <span>{t(vm.etat === "plante" ? "vm.crashedHelp" : "vm.blockedHelp")}</span>
        </div>
      )}

      <div className="nx-vm-sum">
        <div className="nx-vm-sum-side">
          <section className="nx-card" aria-labelledby="vm-config">
            <div className="nx-cardhead"><h2 id="vm-config">{t("vm.config")}</h2><button type="button" className="nx-linkbtn" onClick={() => go("hardware")}>{t("tab.hardware")} →</button></div>
            <dl className="nx-dl nx-dl--stack">
              <dt>{t("ns.col.state")}</dt><dd><StatusIndicator kind="vm" wire={vm.etat} />{vm.uptime_s ? <span className="nx-muted"> · {formatUptimeLong(vm.uptime_s, lang)}</span> : null}</dd>
              <dt>{t("ns.node")}</dt><dd className="nx-mono">{node?.nom || vm.node}</dd>
              <dt>{t("vm.os")}</dt><dd>{vm.os || na}</dd>
              <dt>{t("vh.processor")}</dt><dd className="nx-mono">{vm.vcpu} vCPU</dd>
              <dt>{t("ct.memory")}</dt><dd className="nx-mono">{formatSizeMb(vm.memoire_mo, lang)}</dd>
              <dt>{t("vh.disks")}</dt>
              <dd>{disks == null ? loadingDd : disks === false ? na : disks.length === 0 ? <span className="nx-muted">{t("vh.noDisks")}</span> : (
                <ul className="nx-plainlist">{disks.map((d) => <li key={d.cible} className="nx-mono">{d.cible}{d.bus ? ` · ${d.bus}` : ""}{d.source ? <span className="nx-muted"> · {base(d.source)}</span> : d.type === "cdrom" ? <span className="nx-muted"> · {t("vh.emptyDrive")}</span> : null}</li>)}</ul>
              )}</dd>
              <dt>{t("vh.interfaces")}</dt>
              <dd>{net == null ? loadingDd : net === false ? na : net.length === 0 ? <span className="nx-muted">—</span> : (
                <ul className="nx-plainlist">{net.map((i) => <li key={i.mac}><span className="nx-mono">{i.reseau || i.type_source || "—"}</span> <span className="nx-muted nx-mono">{i.mac}</span></li>)}</ul>
              )}</dd>
              <dt>IP</dt><dd className="nx-mono">{vm.ip || <span title={t("ns.ipHelp")}>{t("ns.ipNa")} ⓘ</span>}</dd>
              <dt>{t("vm.ssh")}</dt><dd className="nx-mono">{vm.utilisateur_ssh ? `${vm.utilisateur_ssh}${vm.ip ? `@${vm.ip}` : ""}` : na}</dd>
              <dt>{t("vm.storageType")}</dt><dd>{vm.stockage_zfs ? "ZFS" : "qcow2"}</dd>
              <dt>UUID</dt><dd className="nx-mono nx-break">{vm.uuid || na}</dd>
            </dl>
          </section>

          <section className="nx-card" aria-labelledby="vm-protection">
            <h2 id="vm-protection">{t("vm.protection")}</h2>
            <dl className="nx-dl nx-dl--stack">
              <dt>{t("vm.lastBackup")}</dt><dd>{backups == null ? loadingDd : lastBackup ? <span><StatusIndicator kind="task" wire={lastBackup.statut === "termine" || lastBackup.statut === "succes" ? "termine" : lastBackup.statut === "echec" ? "echec" : "en_cours"} compact /> <span className="nx-mono">{new Date(lastBackup.cree_le).toLocaleString(lang, { dateStyle: "short", timeStyle: "short" })}</span></span> : <span className="nx-muted">{t("vm.noBackup")}</span>}</dd>
              <dt>{t("vb.schedule")}</dt><dd>{schedule === undefined ? loadingDd : schedule ? <span>{t(`vb.f.${schedule.frequence}`)} · <span className="nx-mono">{schedule.heure} UTC</span></span> : <span className="nx-muted">{t("vm.noSchedule")}</span>}</dd>
              <dt>HA</dt><dd>{ha == null ? loadingDd : ha ? t("vm.haOn") : <span className="nx-muted">{t("vm.haOff")}</span>}</dd>
            </dl>
          </section>
        </div>

        <div className="nx-vm-sum-main">
          <section className="nx-card" aria-labelledby="vm-perf">
            <div className="nx-cardhead"><h2 id="vm-perf">{t("vm.perfLastHour")}</h2><button type="button" className="nx-linkbtn" onClick={() => go("perf")}>{t("vm.perfMore")} →</button></div>
            <VmChartGrid vm={vm} hist={hist} height={130} />
          </section>

          <section className="nx-card" aria-labelledby="vm-snaps">
            <div className="nx-cardhead">
              <h2 id="vm-snaps">{t("tab.snapshots")} <span className="nx-count">{snaps ? snaps.length : "…"}</span></h2>
              <button type="button" className="nx-linkbtn" onClick={() => go("snapshots")}>{t("vm.manage")} →</button>
            </div>
            {snaps == null ? <p className="nx-muted" role="status">{t("loading")}</p> : snapList.length === 0 ? <p className="nx-muted" role="status">{t("vs.none")}</p> : (
              <div className="nx-tablewrap">
                <table className="nx-table">
                  <thead><tr><th scope="col">{t("ct.name")}</th><th scope="col">{t("vs.kind")}</th><th scope="col">{t("vm.created")}</th><th scope="col">{t("vm.description")}</th></tr></thead>
                  <tbody>
                    {snapList.slice(0, SNAP_ROWS).map((s) => (
                      <tr key={s.nom}>
                        <th scope="row" className="nx-mono">{s.nom} {s.actuel && <span className="nx-tag">{t("vs.current")}</span>}</th>
                        <td>{snapKind(s)}</td>
                        <td className="nx-mono">{formatDateTime(s.date_creation, lang) || "—"}</td>
                        <td>{s.description || <span className="nx-muted">—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {snapList.length > SNAP_ROWS && <p className="nx-muted nx-hint">{t("vm.moreSnaps", { n: snapList.length - SNAP_ROWS })}</p>}
              </div>
            )}
          </section>

          <section className="nx-card" aria-labelledby="vm-activity">
            <h2 id="vm-activity">{t("ns.activity")}</h2>
            {recent == null ? <p className="nx-muted">{t("loading")}</p> : recent.length === 0 ? <p className="nx-muted" role="status">{t("dock.none")}</p> : (
              <ul className="nx-list">
                {recent.map((r) => (
                  <li key={r.id}><StatusIndicator kind="task" wire={r.statut} compact /><span>{taskLabel(r.type)}{r.erreur ? <span className="nx-muted"> — {r.erreur}</span> : null}</span><span className="nx-mono nx-muted">{clockTime(r.debut_le || r.cree_le, lang)}</span></li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      <details className="nx-card nx-ops">
        <summary>{t("vm.allOps")}</summary>
        <p className="nx-muted">{t("vm.allOpsHelp")}</p>
        <VMSummaryTab resource={vm} selection={selection} />
      </details>
    </div>
  );
}
