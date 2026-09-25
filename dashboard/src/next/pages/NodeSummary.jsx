import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchHostMetricsHistory, fetchNodeCapabilitiesById, fetchTasks } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { capabilities } from "../lib/capabilities";
import { deriveAlerts } from "../lib/alerts";
import { taskLabel } from "../lib/enums";
import { formatSizeMb, formatSizeGb, formatUptimeLong, clockTime, formatVersionInt } from "../lib/format";
import KpiTile from "../components/KpiTile";
import VmCollection from "../components/VmCollection";
import StatusIndicator from "../components/StatusIndicator";

// An API answer can be null (e.g. a dev proxy that does not relay the route): treat it as empty.
const asList = (v) => (Array.isArray(v) ? v : []);
export default function NodeSummary({ resource: node }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { vms, storagePools, navigateTo } = useInfraStore(useShallow((s) => ({ vms: s.vms, storagePools: s.storagePools, navigateTo: s.navigateTo })));
  const role = useAuthStore((s) => s.role);
  const caps = capabilities(role);
  const [rows, setRows] = useState([]);
  const [hostCaps, setHostCaps] = useState(null);
  const [recent, setRecent] = useState(null);
  const isLocal = node?.id === "local";


  usePolling(async () => { if (isLocal) setRows(asList(await fetchHostMetricsHistory("1h"))); }, 15000, { enabled: !!isLocal });
  useEffect(() => { if (isLocal) fetchHostMetricsHistory("1h").then((r) => setRows(asList(r))).catch(() => {}); }, [isLocal]);
  useEffect(() => { if (node) fetchNodeCapabilitiesById(node.id).then(setHostCaps).catch(() => setHostCaps(null)); }, [node]);
  usePolling(async () => { setRecent(asList(await fetchTasks({ limit: 8, tri: "cree_le", ordre: "desc" }))); }, 10000);
  useEffect(() => { fetchTasks({ limit: 8, tri: "cree_le", ordre: "desc" }).then((r) => setRecent(asList(r))).catch(() => setRecent([])); }, []);

  const nodeVms = useMemo(() => vms.filter((v) => v.node === node?.id), [vms, node]);
  if (!node) return null;

  const last = rows[rows.length - 1];
  const cpu = last?.cpu_pct != null ? last.cpu_pct : null;
  const memRatio = last?.mem_total_mb ? last.mem_used_mb / last.mem_total_mb : null;
  const stoRatio = node.stockage_total_go && node.stockage_utilise_go != null ? node.stockage_utilise_go / node.stockage_total_go : null;
  const na = t("ns.notReported");
  const alerts = deriveAlerts({ nodes: [node], vms: nodeVms, storagePools: storagePools.filter((p) => p.node === node.id), tasks: [] });
  const running = nodeVms.filter((v) => v.etat === "actif").length;
  const virt = hostCaps?.virtualisation;
  const ip = node.ip;

  return (
    <div className="nx-ns">
      {alerts.length > 0 && (
        <div className={`nx-banner ${alerts.some((a) => a.level === "danger") ? "nx-banner--danger" : ""}`} role="status" style={{ borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-default)" }}>
          <StatusIndicator override={{ key: alerts.some((a) => a.level === "danger") ? "health.crit" : "health.warn", shape: alerts.some((a) => a.level === "danger") ? "diamond" : "triangle", tone: alerts.some((a) => a.level === "danger") ? "danger" : "warning" }} compact />
          <strong>{t(alerts.some((a) => a.level === "danger") ? "health.crit" : "health.warn", { n: alerts.length })}</strong>
          <span className="nx-mono">{alerts.slice(0, 3).map((a) => a.text).join(" · ")}</span>
          <button type="button" className="nx-btn nx-btn--ghost" style={{ marginLeft: "auto" }} onClick={() => window.dispatchEvent(new CustomEvent("nx:dock", { detail: "alerts" }))}>{t("ns.viewAlerts")}</button>
        </div>
      )}
      <div className="nx-grid nx-grid--4" role="group" aria-label={t("ns.resources")}>
        <KpiTile label={t("ns.cpu")} ratio={cpu != null ? cpu / 100 : null} tone="accent" sub={hostCaps?.cpu?.coeurs_logiques ? t("ns.cores", { n: hostCaps.cpu.coeurs_logiques }) : ""} unavailable={isLocal ? (cpu == null ? t("ns.collecting") : null) : na} />
        <KpiTile label={t("ns.memory")} ratio={memRatio} tone="success" sub={last?.mem_total_mb ? `${formatSizeMb(last.mem_used_mb, lang)} / ${formatSizeMb(last.mem_total_mb, lang)}` : ""} unavailable={isLocal ? (memRatio == null ? t("ns.collecting") : null) : na} />
        <KpiTile label={t("ns.storage")} ratio={stoRatio} tone="warning" sub={stoRatio != null ? `${formatSizeGb(node.stockage_utilise_go, lang)} / ${formatSizeGb(node.stockage_total_go, lang)}` : ""} unavailable={stoRatio == null ? na : null} />
        <KpiTile label={t("ns.network")} unavailable={t("ns.networkNa")} />
      </div>

      <div className="nx-cols">
        <VmCollection vms={nodeVms} headingId="ns-vms" />

        <section className="nx-card" aria-labelledby="ns-health">
          <h2 id="ns-health">{t("ns.health")}</h2>
          <p><StatusIndicator kind="node" wire={node.etat} compact /> <span className="nx-muted">{node.etat === "online" ? t("ns.healthOk") : t("res.offlineNode")}</span></p>
          <dl className="nx-dl">
            <dt>{t("ns.running")}</dt><dd className="nx-mono">{running} / {nodeVms.length}</dd>
            <dt>{t("ns.uptime")}</dt><dd className="nx-mono">{formatUptimeLong(node.uptime_s, lang) || na}</dd>
            <dt>{t("ns.hypervisor")}</dt><dd className="nx-mono">{virt ? [`${virt.hyperviseur || "QEMU"} ${formatVersionInt(virt.version_hyperviseur) || ""}`.trim(), formatVersionInt(virt.version_libvirt) ? `libvirt ${formatVersionInt(virt.version_libvirt)}` : null].filter(Boolean).join(" · ") : node.version || na}</dd>
            <dt>{t("ns.address")}</dt><dd className="nx-mono">{ip || <span title={t("ns.nodeIpHelp")}>{na} ⓘ</span>}</dd>
            {hostCaps?.cpu?.modele && (<><dt>{t("ns.cpuModel")}</dt><dd>{hostCaps.cpu.modele}</dd></>)}
          </dl>
          {caps.hostShell && isLocal && <button type="button" className="nx-btn" onClick={() => navigateTo("node", node.id, "shell")}>{t("ns.openShell")}</button>}
        </section>
      </div>

      <div className="nx-cols nx-cols--even">
        <section className="nx-card" aria-labelledby="ns-activity">
          <h2 id="ns-activity">{t("ns.activity")}</h2>
          {recent == null ? <p className="nx-muted">{t("loading")}</p> : recent.length === 0 ? <p className="nx-muted" role="status">{t("dock.none")}</p> : (
            <ul className="nx-list">
              {recent.slice(0, 6).map((r) => (
                <li key={r.id}><StatusIndicator kind="task" wire={r.statut} compact /><span>{taskLabel(r.type)} <span className="nx-mono nx-muted">{r.cible || ""}</span></span><span className="nx-mono nx-muted">{clockTime(r.debut_le || r.cree_le, lang)}</span></li>
              ))}
            </ul>
          )}
          <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("datacenter", null, "activity")}>{t("ns.allActivity")}</button>
        </section>
        <section className="nx-card" aria-labelledby="ns-alerts">
          <h2 id="ns-alerts">{t("dock.alerts")}</h2>
          {alerts.length === 0 ? <p role="status"><StatusIndicator override={{ key: "health.ok", shape: "dot", tone: "success" }} /> <span className="nx-muted">{t("ns.noIncident")}</span></p> : (
            <ul className="nx-list">{alerts.map((a) => <li key={a.id}><StatusIndicator override={{ key: a.level === "danger" ? "state.crashed" : a.level === "warning" ? "state.degraded" : "state.offline", shape: a.level === "danger" ? "diamond" : a.level === "warning" ? "triangle" : "ring", tone: a.level }} compact /><span className="nx-mono">{a.text}</span><span className="nx-muted">{a.kind.replace(/-/g, " ")}</span></li>)}</ul>
          )}
          <button type="button" className="nx-btn nx-btn--ghost" onClick={() => window.dispatchEvent(new CustomEvent("nx:dock", { detail: "alerts" }))}>{t("ns.viewAlerts")}</button>
        </section>
      </div>
    </div>
  );
}
