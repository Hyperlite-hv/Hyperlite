import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchHostMetricsHistory, fetchTasks } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { useFreshness } from "../lib/inventory";
import { deriveAlerts } from "../lib/alerts";
import { taskLabel } from "../lib/enums";
import { formatSizeGb, formatSizeMb, formatUptimeLong, clockTime } from "../lib/format";
import KpiTile from "../components/KpiTile";
import StatTile from "../components/StatTile";
import StatusIndicator from "../components/StatusIndicator";

const asList = (v) => (Array.isArray(v) ? v : []);
const levelOf = (r) => (r >= 0.9 ? "danger" : r >= 0.8 ? "warning" : "accent");

// Datacenter overview: is the infrastructure healthy, what needs attention, how full is it,
// what just happened. Every figure comes from the API; unavailable ones say so.
export default function Overview() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { nodes, vms, storagePools, tasks, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, tasks: s.tasks, navigateTo: s.navigateTo })));
  const containers = useFreshness((s) => s.containers);
  const [rows, setRows] = useState([]);
  const [recent, setRecent] = useState(null);

  usePolling(async () => { setRows(asList(await fetchHostMetricsHistory("1h"))); }, 15000);
  useEffect(() => { fetchHostMetricsHistory("1h").then((r) => setRows(asList(r))).catch(() => {}); }, []);
  usePolling(async () => { setRecent(asList(await fetchTasks({ limit: 8, tri: "cree_le", ordre: "desc" }))); }, 10000);
  useEffect(() => { fetchTasks({ limit: 8, tri: "cree_le", ordre: "desc" }).then((r) => setRecent(asList(r))).catch(() => setRecent([])); }, []);

  const alerts = useMemo(() => deriveAlerts({ nodes, vms, storagePools, tasks }), [nodes, vms, storagePools, tasks]);
  const online = nodes.filter((n) => n.etat === "online").length;
  const running = vms.filter((v) => v.etat === "actif").length;
  const problems = vms.filter((v) => ["plante", "bloque", "inconnu"].includes(v.etat)).length;
  const last = rows[rows.length - 1];
  const cpu = last?.cpu_pct != null ? last.cpu_pct / 100 : null;
  const mem = last?.mem_total_mb ? last.mem_used_mb / last.mem_total_mb : null;
  const totalGb = nodes.reduce((a, n) => a + (n.stockage_total_go || 0), 0);
  const usedGb = nodes.reduce((a, n) => a + (n.stockage_utilise_go || 0), 0);
  const sto = totalGb ? usedGb / totalGb : null;
  const critical = alerts.some((a) => a.level === "danger");
  const na = t("ns.notReported");
  const cpuSeries = rows.slice(-40).map((r) => r.cpu_pct);
  const memSeries = rows.slice(-40).map((r) => (r.mem_total_mb ? (r.mem_used_mb / r.mem_total_mb) * 100 : null));

  return (
    <div className="nx-ns">
      {alerts.length > 0 && (
        <div className={`nx-banner ${critical ? "nx-banner--danger" : ""}`} role="status" style={{ borderRadius: "var(--radius-md)", border: "1px solid var(--color-border-default)" }}>
          <StatusIndicator override={{ key: critical ? "health.crit" : "health.warn", shape: critical ? "diamond" : "triangle", tone: critical ? "danger" : "warning" }} compact />
          <strong>{t(critical ? "health.crit" : "health.warn", { n: alerts.length })}</strong>
          <span className="nx-mono">{alerts.slice(0, 3).map((a) => a.text).join(" · ")}</span>
          <button type="button" className="nx-btn nx-btn--ghost" style={{ marginLeft: "auto" }} onClick={() => window.dispatchEvent(new CustomEvent("nx:dock", { detail: "alerts" }))}>{t("ns.viewAlerts")}</button>
        </div>
      )}

      <div className="nx-grid nx-grid--4" role="group" aria-label={t("ov.inventory")}>
        <StatTile label={t("nav.nodes")} value={`${online} / ${nodes.length}`} sub={t("ov.nodesOnline")} onClick={() => navigateTo("datacenter", null, "nodes")} />
        <StatTile label={t("nav.vms")} value={`${running} / ${vms.length}`} sub={problems ? t("ov.vmsProblems", { n: problems }) : t("ov.vmsRunning")} onClick={() => navigateTo("datacenter", null, "vms")} />
        <StatTile label={t("nav.containers")} value={containers.length} sub={t("ov.containersSub", { n: containers.filter((c) => c.etat === "actif").length })} onClick={() => navigateTo("datacenter", null, "containers")} />
        <StatTile label={t("nav.storage")} value={storagePools.length} sub={t("ov.poolsSub")} onClick={() => navigateTo("datacenter", null, "storage")} />
      </div>

      <div className="nx-grid nx-grid--4" role="group" aria-label={t("ns.resources")}>
        <KpiTile label={t("ns.cpu")} ratio={cpu} sub={cpu != null ? t("ov.localHost") : ""} unavailable={cpu == null ? t("ns.collecting") : null} />
        <KpiTile label={t("ns.memory")} ratio={mem} sub={last?.mem_total_mb ? `${formatSizeMb(last.mem_used_mb, lang)} / ${formatSizeMb(last.mem_total_mb, lang)}` : ""} tone="info" unavailable={mem == null ? t("ns.collecting") : null} />
        <KpiTile label={t("ns.storage")} ratio={sto} sub={sto != null ? `${formatSizeGb(usedGb, lang)} / ${formatSizeGb(totalGb, lang)}` : ""} tone="success" unavailable={sto == null ? na : null} />
        <StatTile label={t("ns.network")} unavailable={t("ns.networkNa")} />
      </div>
      {(cpuSeries.length > 1 || memSeries.length > 1) && (
        <div className="nx-cols nx-cols--even">
          <StatTile label={`${t("ns.cpu")} · ${t("ov.lastHour")}`} series={cpuSeries} tone={levelOf(cpu ?? 0)} value={cpu != null ? `${Math.round(cpu * 100)} %` : null} sub={t("ov.localHost")} />
          <StatTile label={`${t("ns.memory")} · ${t("ov.lastHour")}`} series={memSeries} tone={levelOf(mem ?? 0)} value={mem != null ? `${Math.round(mem * 100)} %` : null} sub={t("ov.localHost")} />
        </div>
      )}

      <div className="nx-cols">
        <section className="nx-card" aria-labelledby="ov-nodes">
          <div className="nx-cardhead"><h2 id="ov-nodes">{t("nav.nodes")} <span className="nx-count">{nodes.length}</span></h2>
            <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("datacenter", null, "nodes")}>{t("ov.manage")}</button></div>
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ns.col.name")}</th><th scope="col" className="nx-num">{t("ns.running")}</th><th scope="col">{t("ns.storage")}</th><th scope="col">{t("ns.col.uptime")}</th></tr></thead>
              <tbody>
                {nodes.map((n) => {
                  const nv = vms.filter((v) => v.node === n.id);
                  const r = n.stockage_total_go ? (n.stockage_utilise_go || 0) / n.stockage_total_go : null;
                  return (
                    <tr key={n.id}>
                      <td><StatusIndicator kind="node" wire={n.etat} /></td>
                      <th scope="row"><button type="button" className="nx-link" onClick={() => navigateTo("node", n.id, "summary")}>{n.nom}</button></th>
                      <td className="nx-num nx-mono">{nv.filter((v) => v.etat === "actif").length} / {nv.length}</td>
                      <td>{r == null ? <span className="nx-muted">{na}</span> : <span className="nx-mono">{Math.round(r * 100)} % <span className="nx-progress nx-progress--inline" role="meter" aria-label={t("ns.storage")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(r * 100)}><span style={{ width: `${Math.round(r * 100)}%`, background: `var(--color-${levelOf(r) === "accent" ? "accent" : levelOf(r)})` }} /></span></span>}</td>
                      <td className="nx-mono">{formatUptimeLong(n.uptime_s, lang) || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="nx-card" aria-labelledby="ov-attn">
          <h2 id="ov-attn">{t("ov.attention")}</h2>
          {alerts.length === 0 ? <p role="status"><StatusIndicator override={{ key: "health.ok", shape: "dot", tone: "success" }} /> <span className="nx-muted">{t("ns.noIncident")}</span></p> : (
            <ul className="nx-list">{alerts.slice(0, 6).map((a) => (
              <li key={a.id}><StatusIndicator override={{ key: a.level === "danger" ? "state.crashed" : a.level === "warning" ? "state.degraded" : "state.offline", shape: a.level === "danger" ? "diamond" : a.level === "warning" ? "triangle" : "ring", tone: a.level }} compact /><span className="nx-mono">{a.text}</span>{a.target ? <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo(a.target.type, a.target.id, a.target.tab)}>{t("menu.open")}</button> : <span />}</li>
            ))}</ul>
          )}
        </section>
      </div>

      <div className="nx-cols nx-cols--even">
        <section className="nx-card" aria-labelledby="ov-pools">
          <div className="nx-cardhead"><h2 id="ov-pools">{t("inv.storage")}</h2><button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("datacenter", null, "storage")}>{t("ov.manage")}</button></div>
          {storagePools.length === 0 ? <p className="nx-muted" role="status">{t("ov.noPools")}</p> : (
            <ul className="nx-list nx-list--pools">
              {storagePools.map((p) => {
                const r = p.capacite_go ? (p.capacite_go - (p.disponible_go ?? p.capacite_go)) / p.capacite_go : null;
                return (
                  <li key={`${p.node}:${p.nom}`}>
                    <StatusIndicator kind="pool" wire={p.etat} compact />
                    <span><span className="nx-mono">{p.nom}</span> <span className="nx-muted">{p.type}</span></span>
                    <span className="nx-mono">{r == null ? na : `${Math.round(r * 100)} % · ${formatSizeGb(p.disponible_go, lang)} ${t("ov.free")}`}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="nx-card" aria-labelledby="ov-activity">
          <div className="nx-cardhead"><h2 id="ov-activity">{t("ns.activity")}</h2><button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("datacenter", null, "activity")}>{t("ns.allActivity")}</button></div>
          {recent == null ? <p className="nx-muted">{t("loading")}</p> : recent.length === 0 ? <p className="nx-muted" role="status">{t("dock.none")}</p> : (
            <ul className="nx-list">
              {recent.slice(0, 6).map((r) => (
                <li key={r.id}><StatusIndicator kind="task" wire={r.statut} compact /><span>{taskLabel(r.type)} <span className="nx-mono nx-muted">{r.cible || ""}</span></span><span className="nx-mono nx-muted">{clockTime(r.debut_le || r.cree_le, lang)}</span></li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
