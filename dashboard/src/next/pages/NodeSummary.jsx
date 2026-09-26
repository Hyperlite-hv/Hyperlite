import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchNodeCapabilitiesById, fetchTasks } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { deriveAlerts } from "../lib/alerts";
import { taskLabel } from "../lib/enums";
import { formatSizeMb, formatSizeGb, formatUptimeLong, clockTime, formatVersionInt } from "../lib/format";
import VmCollection from "../components/VmCollection";
import StatusIndicator from "../components/StatusIndicator";
import { useHostHistory, ChartGrid, NODE_CHARTS } from "./VmPerformance";

// An API answer can be null (e.g. a dev proxy that does not relay the route): treat it as empty.
const asList = (v) => (Array.isArray(v) ? v : []);
// Node summary, same layout as the VM page: configuration and alerts on the left, the last hour of the host
// charts, its VMs and the recent activity on the right. Remote nodes are not sampled: their charts say so.
export default function NodeSummary({ resource: node }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { vms, storagePools, navigateTo } = useInfraStore(useShallow((s) => ({ vms: s.vms, storagePools: s.storagePools, navigateTo: s.navigateTo })));
  const [hostCaps, setHostCaps] = useState(null);
  const [recent, setRecent] = useState(null);
  const nodeId = node?.id;
  const isLocal = nodeId === "local";

  const hist = useHostHistory(node, "1h");
  useEffect(() => { if (nodeId) fetchNodeCapabilitiesById(nodeId).then(setHostCaps).catch(() => setHostCaps(null)); }, [nodeId]);
  usePolling(async () => { setRecent(asList(await fetchTasks({ limit: 8, tri: "cree_le", ordre: "desc" }))); }, 10000);
  useEffect(() => { fetchTasks({ limit: 8, tri: "cree_le", ordre: "desc" }).then((r) => setRecent(asList(r))).catch(() => setRecent([])); }, []);

  const nodeVms = useMemo(() => vms.filter((v) => v.node === node?.id), [vms, node]);
  if (!node) return null;

  const last = [...(hist.rows || [])].reverse().find((r) => r.mem_total_mb);
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
      <div className="nx-vm-sum">
        <div className="nx-vm-sum-side">
          <section className="nx-card" aria-labelledby="ns-config">
            <div className="nx-cardhead"><h2 id="ns-config">{t("vm.config")}</h2><button type="button" className="nx-linkbtn" onClick={() => navigateTo("node", node.id, "system")}>{t("tab.system")} →</button></div>
            <dl className="nx-dl nx-dl--stack">
              <dt>{t("ns.col.state")}</dt><dd><StatusIndicator kind="node" wire={node.etat} /></dd>
              <dt>{t("ns.address")}</dt><dd className="nx-mono">{ip || <span title={t("ns.nodeIpHelp")}>{na} ⓘ</span>}</dd>
              <dt>{t("ns.uptime")}</dt><dd className="nx-mono">{formatUptimeLong(node.uptime_s, lang) || na}</dd>
              <dt>{t("ns.hypervisor")}</dt><dd className="nx-mono">{virt ? [`${virt.hyperviseur || "QEMU"} ${formatVersionInt(virt.version_hyperviseur) || ""}`.trim(), formatVersionInt(virt.version_libvirt) ? `libvirt ${formatVersionInt(virt.version_libvirt)}` : null].filter(Boolean).join(" · ") : node.version || na}</dd>
              <dt>{t("ns.cpuModel")}</dt><dd>{hostCaps?.cpu?.modele || na}{hostCaps?.cpu?.coeurs_logiques ? <span className="nx-muted"> · {t("ns.cores", { n: hostCaps.cpu.coeurs_logiques })}</span> : null}</dd>
              <dt>{t("ns.memory")}</dt><dd className="nx-mono">{last?.mem_total_mb ? `${formatSizeMb(last.mem_used_mb, lang)} / ${formatSizeMb(last.mem_total_mb, lang)}` : isLocal ? t("ns.collecting") : na}</dd>
              <dt>{t("ns.storage")}</dt><dd>{stoRatio != null ? <><span className="nx-mono">{formatSizeGb(node.stockage_utilise_go, lang)} / {formatSizeGb(node.stockage_total_go, lang)}</span><span className="nx-progress nx-progress--inline" role="meter" aria-label={t("ns.storage")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(stoRatio * 100)}><span style={{ width: `${Math.round(stoRatio * 100)}%`, background: `var(--color-${stoRatio >= 0.9 ? "danger" : stoRatio >= 0.8 ? "warning" : "info"})` }} /></span></> : na}</dd>
              <dt>{t("ns.running")}</dt><dd className="nx-mono">{running} / {nodeVms.length}</dd>
            </dl>
          </section>

          <section className="nx-card" aria-labelledby="ns-alerts">
            <h2 id="ns-alerts">{t("dock.alerts")}</h2>
            {alerts.length === 0 ? <p role="status"><StatusIndicator override={{ key: "health.ok", shape: "dot", tone: "success" }} /> <span className="nx-muted">{t("ns.noIncident")}</span></p> : (
              <ul className="nx-list">{alerts.map((a) => <li key={a.id}><StatusIndicator override={{ key: a.level === "danger" ? "state.crashed" : a.level === "warning" ? "state.degraded" : "state.offline", shape: a.level === "danger" ? "diamond" : a.level === "warning" ? "triangle" : "ring", tone: a.level }} compact /><span className="nx-mono">{a.text}</span><span className="nx-muted">{a.kind.replace(/-/g, " ")}</span></li>)}</ul>
            )}
            <button type="button" className="nx-btn nx-btn--ghost" onClick={() => window.dispatchEvent(new CustomEvent("nx:dock", { detail: "alerts" }))}>{t("ns.viewAlerts")}</button>
          </section>
        </div>

        <div className="nx-vm-sum-main">
          <section className="nx-card" aria-labelledby="ns-perf">
            <div className="nx-cardhead"><h2 id="ns-perf">{t("vm.perfLastHour")}</h2><button type="button" className="nx-linkbtn" onClick={() => navigateTo("node", node.id, "perf")}>{t("vm.perfMore")} →</button></div>
            <ChartGrid hist={hist} running={node.etat === "online"} charts={NODE_CHARTS} remoteKey="node.remoteNoMetrics" height={130} />
          </section>

          <VmCollection vms={nodeVms} headingId="ns-vms" />

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
        </div>
      </div>
    </div>
  );
}
