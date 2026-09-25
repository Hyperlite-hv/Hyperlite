import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchSnapshots, fetchVMBackups, fetchHaProtected, fetchTasks } from "../../api/client";
import { useLiveVMMetrics } from "../../hooks/useLiveVMMetrics";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { taskLabel } from "../lib/enums";
import { formatSizeMb, formatUptimeLong, clockTime } from "../lib/format";
import KpiTile from "../components/KpiTile";
import StatusIndicator from "../components/StatusIndicator";
import VMSummaryTab from "../../panels/vm/VMSummaryTab";

const asList = (v) => (Array.isArray(v) ? v : []);
const rate = (kbs, lang) => (kbs == null ? null : kbs >= 1024 ? `${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(kbs / 1024)} MB/s` : `${Math.round(kbs)} KB/s`);

// VM summary: state first, live capacity with trends, identity, protection and activity of this VM.
// Every operation of the historical screen (clone, template, migrate, HA, auto-clean-up, delete...)
// stays available in the "All operations" section until each one is rebuilt.
export default function VmSummary({ resource: vm, selection }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const nodes = useInfraStore(useShallow((s) => s.nodes));
  const running = vm?.etat === "actif";
  const isLocal = vm?.node === "local";
  const { data, current } = useLiveVMMetrics(vm?.nom, !!vm && running && isLocal);
  const [snaps, setSnaps] = useState(null);
  const [backups, setBackups] = useState(null);
  const [ha, setHa] = useState(null);
  const [recent, setRecent] = useState(null);

  useEffect(() => {
    if (!vm) return;
    setSnaps(null); setBackups(null); setHa(null);
    fetchSnapshots(vm.nom).then((r) => setSnaps(asList(r))).catch(() => setSnaps([]));
    fetchVMBackups(vm.nom).then((r) => setBackups(asList(r))).catch(() => setBackups([]));
    fetchHaProtected().then((r) => setHa(asList(r).some((x) => x.vm_name === vm.nom))).catch(() => setHa(false));
  }, [vm?.nom]); // eslint-disable-line react-hooks/exhaustive-deps
  usePolling(async () => { if (vm) setRecent(asList(await fetchTasks({ cible: vm.nom, limit: 6, tri: "cree_le", ordre: "desc" }))); }, 10000, { enabled: !!vm });
  useEffect(() => { if (vm) fetchTasks({ cible: vm.nom, limit: 6, tri: "cree_le", ordre: "desc" }).then((r) => setRecent(asList(r))).catch(() => setRecent([])); }, [vm?.nom]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!vm) return null;
  const node = nodes.find((n) => n.id === vm.node);
  const na = t("ns.notReported");
  const stopped = t("vm.stoppedNoLive");
  const unavailable = (v) => (!isLocal ? t("vm.remoteNoMetrics") : !running ? stopped : v);
  const cpu = current ? current.cpu * 100 : null;
  const ram = current ? current.ram : null;
  const diskRead = current ? (current.disques || []).reduce((a, d) => a + (d.lecture_ko_s || 0), 0) : null;
  const diskWrite = current ? (current.disques || []).reduce((a, d) => a + (d.ecriture_ko_s || 0), 0) : null;
  const problem = vm.etat === "plante" || vm.etat === "bloque";
  const lastBackup = backups && backups.length ? [...backups].sort((a, b) => String(b.cree_le).localeCompare(String(a.cree_le)))[0] : null;

  return (
    <div className="nx-ns">
      {problem && (
        <div className={`nx-banner ${vm.etat === "plante" ? "nx-banner--danger" : ""}`} role="status" style={{ borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-default)" }}>
          <StatusIndicator kind="vm" wire={vm.etat} />
          <span>{t(vm.etat === "plante" ? "vm.crashedHelp" : "vm.blockedHelp")}</span>
        </div>
      )}

      <div className="nx-grid nx-grid--4" role="group" aria-label={t("ns.resources")}>
        <KpiTile label={t("ns.cpu")} value={cpu != null ? `${Math.round(cpu)} %` : null} ratio={cpu != null ? cpu / 100 : null} sub={t("vm.allocVcpu", { n: vm.vcpu })} series={data.slice(-40).map((p) => p.cpu * 100)} unavailable={unavailable(cpu == null ? t("ns.collecting") : null)} />
        <KpiTile label={t("ns.memory")} value={ram != null ? `${Math.round(ram * 100)} %` : null} ratio={ram} sub={current?.ramAlloueeMo ? `${formatSizeMb(current.ramUseeMo, lang)} / ${formatSizeMb(current.ramAlloueeMo, lang)}` : t("vm.allocRam", { n: formatSizeMb(vm.memoire_mo, lang) })} series={data.slice(-40).map((p) => p.ram * 100)} unavailable={unavailable(ram == null ? t("ns.collecting") : null)} />
        <KpiTile label={t("vm.disk")} value={diskRead != null ? rate(diskRead + diskWrite, lang) : null} sub={diskRead != null ? `↓ ${rate(diskRead, lang)} · ↑ ${rate(diskWrite, lang)}` : ""} series={data.slice(-40).map((p) => (p.disques || []).reduce((a, d) => a + (d.lecture_ko_s || 0) + (d.ecriture_ko_s || 0), 0))} unavailable={unavailable(diskRead == null ? t("ns.collecting") : null)} />
        <KpiTile label={t("ns.network")} value={current ? rate(current.netIn + current.netOut, lang) : null} sub={current ? `↓ ${rate(current.netIn, lang)} · ↑ ${rate(current.netOut, lang)}` : ""} series={data.slice(-40).map((p) => p.netIn + p.netOut)} unavailable={unavailable(current == null ? t("ns.collecting") : null)} />
      </div>

      <div className="nx-cols">
        <section className="nx-card" aria-labelledby="vm-identity">
          <h2 id="vm-identity">{t("vm.identity")}</h2>
          <dl className="nx-dl">
            <dt>{t("ns.col.state")}</dt><dd><StatusIndicator kind="vm" wire={vm.etat} />{vm.uptime_s ? <span className="nx-muted"> · {formatUptimeLong(vm.uptime_s, lang)}</span> : null}</dd>
            <dt>{t("ns.node")}</dt><dd className="nx-mono">{node?.nom || vm.node}</dd>
            <dt>IP</dt><dd className="nx-mono">{vm.ip || <span title={t("ns.ipHelp")}>{t("ns.ipNa")} ⓘ</span>}</dd>
            <dt>{t("vm.ssh")}</dt><dd className="nx-mono">{vm.utilisateur_ssh ? `${vm.utilisateur_ssh}${vm.ip ? `@${vm.ip}` : ""}` : na}</dd>
            <dt>{t("vm.os")}</dt><dd>{vm.os || na}</dd>
            <dt>UUID</dt><dd className="nx-mono">{vm.uuid || na}</dd>
            <dt>{t("vm.resources")}</dt><dd className="nx-mono">{t("ns.vmSpec", { cpu: vm.vcpu, ram: formatSizeMb(vm.memoire_mo, lang) })}</dd>
            <dt>{t("vm.storageType")}</dt><dd>{vm.stockage_zfs ? "ZFS" : "qcow2"}</dd>
          </dl>
        </section>

        <section className="nx-card" aria-labelledby="vm-protection">
          <h2 id="vm-protection">{t("vm.protection")}</h2>
          <dl className="nx-dl">
            <dt>{t("vm.snapshots")}</dt><dd className="nx-mono">{snaps == null ? "…" : snaps.length}</dd>
            <dt>{t("vm.lastBackup")}</dt><dd>{backups == null ? "…" : lastBackup ? <span><StatusIndicator kind="task" wire={lastBackup.statut === "termine" || lastBackup.statut === "succes" ? "termine" : lastBackup.statut === "echec" ? "echec" : "en_cours"} compact /> <span className="nx-mono">{clockTime(lastBackup.cree_le, lang)}</span></span> : <span className="nx-muted">{t("vm.noBackup")}</span>}</dd>
            <dt>HA</dt><dd>{ha == null ? "…" : ha ? t("vm.haOn") : <span className="nx-muted">{t("vm.haOff")}</span>}</dd>
          </dl>
        </section>
      </div>

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

      <details className="nx-card nx-ops">
        <summary>{t("vm.allOps")}</summary>
        <p className="nx-muted">{t("vm.allOpsHelp")}</p>
        <VMSummaryTab resource={vm} selection={selection} />
      </details>
    </div>
  );
}
