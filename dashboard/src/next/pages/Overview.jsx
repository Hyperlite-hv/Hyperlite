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
import LineChart from "../components/LineChart";
import StatusIndicator from "../components/StatusIndicator";

const asList = (v) => (Array.isArray(v) ? v : []);
const levelOf = (r) => (r >= 0.9 ? "danger" : r >= 0.8 ? "warning" : "info");
const RANGES = ["1h", "24h", "7j", "30j"];
const nowMs = () => Date.now();

function Meter({ ratio, label }) {
  const pct = Math.round(ratio * 100);
  return <span className="nx-progress nx-progress--inline nx-ov-meter" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><span style={{ width: `${pct}%`, background: `var(--color-${levelOf(ratio)})` }} /></span>;
}

function Kpi({ label, value, unit, sub, subTone, ratio, onClick }) {
  return (
    <button type="button" className="nx-card nx-kpi-card" onClick={onClick}>
      <span className="nx-kpi-card-label">{label}</span>
      <span className="nx-kpi-card-value">{value}{unit && <small>{unit}</small>}</span>
      {ratio != null ? <Meter ratio={ratio} label={label} /> : <span className={`nx-kpi-card-sub${subTone ? ` nx-tone-${subTone}` : ""}`}>{sub}</span>}
    </button>
  );
}

// Datacenter overview as an operations summary: five headline figures, host history charts, nodes with their
// load, alerts derived from the inventory, and the operations in progress. Every figure comes from the API;
// what the API does not provide (per-node load of remote nodes, shared-storage IOPS) says so instead of being drawn.
export default function Overview() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { nodes, vms, storagePools, tasks, navigateTo } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, tasks: s.tasks, navigateTo: s.navigateTo })));
  const containers = useFreshness((s) => s.containers);
  const [view, setView] = useState("summary");
  const [range, setRange] = useState("24h");
  const [rows, setRows] = useState([]);
  const [recent, setRecent] = useState(null);
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(nowMs);

  const [shown, setShown] = useState("24h");
  // A fresh install has no hourly history yet: fall back to the raw last-hour tier instead of an empty chart.
  const load = async (r) => {
    try {
      let x = asList(await fetchHostMetricsHistory(r)); let used = r;
      if (x.length < 2 && r !== "1h") { x = asList(await fetchHostMetricsHistory("1h")); used = "1h"; }
      setRows(x); setShown(used); setFailed(false);
    } catch { setFailed(true); }
  };
  usePolling(async () => { await load(range); setNow(nowMs()); }, 30000);
  useEffect(() => { load(range); }, [range]);
  usePolling(async () => { setRecent(asList(await fetchTasks({ limit: 20, tri: "cree_le", ordre: "desc" }))); }, 10000);
  useEffect(() => { fetchTasks({ limit: 20, tri: "cree_le", ordre: "desc" }).then((r) => setRecent(asList(r))).catch(() => setRecent([])); }, []);

  const alerts = useMemo(() => deriveAlerts({ nodes, vms, storagePools, tasks }), [nodes, vms, storagePools, tasks]);
  const online = nodes.filter((n) => n.etat === "online").length;
  const running = vms.filter((v) => v.etat === "actif").length;
  const problems = vms.filter((v) => ["plante", "bloque", "inconnu"].includes(v.etat)).length;
  const ctRunning = containers.filter((c) => c.etat === "actif").length;
  const ops = tasks.filter((x) => x.statut === "en_cours");
  const last = rows[rows.length - 1];
  const cpu = last?.cpu_pct != null ? last.cpu_pct / 100 : null;
  const mem = last?.mem_total_mb ? last.mem_used_mb / last.mem_total_mb : null;
  const totalGb = nodes.reduce((a, n) => a + (n.stockage_total_go || 0), 0);
  const usedGb = nodes.reduce((a, n) => a + (n.stockage_utilise_go || 0), 0);
  const sto = totalGb ? usedGb / totalGb : null;
  const na = t("ns.notReported");
  const pctFmt = (r) => new Intl.NumberFormat(lang, { minimumFractionDigits: r < 0.1 ? 1 : 0, maximumFractionDigits: 1 }).format(r * 100);
  const timeFmt = (tk) => new Date(tk).toLocaleString(lang, shown === "7j" || shown === "30j" ? { day: "2-digit", month: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
  const points = (pick) => rows.map((r) => ({ t: new Date(r.ts).getTime(), v: pick(r) })).filter((p) => p.v != null && !Number.isNaN(p.t));
  const cpuSeries = [{ key: "cpu", label: `${t("ns.cpu")} · ${nodes.find((n) => n.id === "local")?.nom || t("ov.localHost")}`, tone: "info", points: points((r) => r.cpu_pct) }];
  const memSeries = [{ key: "mem", label: `${t("ns.memory")} · ${nodes.find((n) => n.id === "local")?.nom || t("ov.localHost")}`, tone: "info2", points: points((r) => (r.mem_total_mb ? (r.mem_used_mb / r.mem_total_mb) * 100 : null)) }];
  const sev = (a) => (a.level === "danger" ? ["ov.sev.incident", "danger"] : a.level === "warning" ? ["ov.sev.attention", "warning"] : ["ov.sev.info", "info"]);
  const opAge = (o) => { const s = Math.max(0, Math.round((now - new Date(o.debut_le || o.cree_le).getTime()) / 1000)); return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`; };

  const cpuCard = (
      <section className="nx-card" aria-labelledby="ov-cpuchart">
        <div className="nx-cardhead"><h2 id="ov-cpuchart">{t("ov.cpuHistory")}</h2><span className="nx-muted nx-cardhead-note">{shown}</span></div>
        {failed ? <p className="nx-muted" role="status">{na}</p> : rows.length < 2 ? <p className="nx-muted" role="status">{t("ns.collecting")}</p> : <LineChart series={cpuSeries} label={t("ov.cpuHistory")} formatTime={timeFmt} />}
      </section>
  );
  const memCard = (
      <section className="nx-card" aria-labelledby="ov-memchart">
        <div className="nx-cardhead"><h2 id="ov-memchart">{t("ov.memHistory")}</h2><span className="nx-muted nx-cardhead-note">{shown}</span></div>
        {failed ? <p className="nx-muted" role="status">{na}</p> : rows.length < 2 ? <p className="nx-muted" role="status">{t("ns.collecting")}</p> : <LineChart series={memSeries} label={t("ov.memHistory")} formatTime={timeFmt} />}
        <p className="nx-hint" style={{ marginBottom: 0 }}>{t("ov.iopsNa")}</p>
      </section>
  );

  return (
    <div className="nx-ns">
      <div className="nx-ov-toolbar">
        <div className="nx-tabs" role="tablist" aria-label={t("ov.views")}>
          {["summary", "perf", "events"].map((v) => <button key={v} type="button" role="tab" aria-selected={view === v} className="nx-tab" onClick={() => setView(v)}>{t(`ov.view.${v}`)}</button>)}
        </div>
        {view === "perf" && (
          <div className="nx-seg" role="group" aria-label={t("ov.range")}>
            {RANGES.map((r) => <button key={r} type="button" aria-pressed={range === r} onClick={() => setRange(r)}>{r}</button>)}
          </div>
        )}
      </div>

      {view === "summary" && (
        <>
          <div className="nx-kpi-row" role="group" aria-label={t("ov.inventory")}>
            <Kpi label={t("nav.nodes")} value={online} unit={` / ${nodes.length}`} sub={online === nodes.length && nodes.length ? t("ov.nodesOnlineAll") : t("ov.nodesOnline")} subTone={online === nodes.length ? "success" : "warning"} onClick={() => navigateTo("datacenter", null, "nodes")} />
            <Kpi label={t("nav.vms")} value={running} unit={` / ${vms.length}`} sub={problems ? t("ov.vmsProblems", { n: problems }) : t("ov.vmsRunning")} subTone={problems ? "warning" : undefined} onClick={() => navigateTo("datacenter", null, "vms")} />
            <Kpi label={t("nav.containers")} value={ctRunning} unit={` / ${containers.length}`} sub={t("ov.containersStopped", { n: containers.length - ctRunning })} onClick={() => navigateTo("datacenter", null, "containers")} />
            {cpu != null ? <Kpi label={t("ov.cpuHost")} value={pctFmt(cpu)} unit=" %" ratio={cpu} onClick={() => setView("perf")} /> : <Kpi label={t("ov.cpuHost")} value="—" sub={t("ns.collecting")} onClick={() => setView("perf")} />}
            {sto != null ? <Kpi label={t("nav.storage")} value={formatSizeGb(usedGb, lang)} unit={` / ${formatSizeGb(totalGb, lang)}`} ratio={sto} onClick={() => navigateTo("datacenter", null, "storage")} /> : <Kpi label={t("nav.storage")} value={storagePools.length} sub={t("ov.poolsSub")} onClick={() => navigateTo("datacenter", null, "storage")} />}
          </div>

          <div className="nx-cols nx-cols--wide">
            {cpuCard}
            {memCard}
          </div>

          <div className="nx-cols nx-cols--wide">
            <section className="nx-card" aria-labelledby="ov-nodes">
              <div className="nx-cardhead"><h2 id="ov-nodes">{t("nav.nodes")} <span className="nx-count">{nodes.length}</span></h2>
                <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("datacenter", null, "nodes")}>{t("ov.seeAll")}</button></div>
              <div className="nx-tablewrap">
                <table className="nx-table">
                  <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ns.col.name")}</th><th scope="col">{t("ns.cpu")}</th><th scope="col">{t("ns.memory")}</th><th scope="col" className="nx-num">VM</th><th scope="col">{t("ns.col.uptime")}</th></tr></thead>
                  <tbody>
                    {nodes.map((n) => {
                      const nv = vms.filter((v) => v.node === n.id);
                      const local = n.id === "local";
                      return (
                        <tr key={n.id}>
                          <td><StatusIndicator kind="node" wire={n.etat} /></td>
                          <th scope="row"><button type="button" className="nx-link" onClick={() => navigateTo("node", n.id, "summary")}>{n.nom}</button></th>
                          <td>{local && cpu != null ? <span className="nx-mono nx-ov-load">{Math.round(cpu * 100)} % <Meter ratio={cpu} label={`${n.nom} ${t("ns.cpu")}`} /></span> : <span className="nx-muted">{na}</span>}</td>
                          <td>{local && mem != null ? <span className="nx-mono nx-ov-load">{Math.round(mem * 100)} % <Meter ratio={mem} label={`${n.nom} ${t("ns.memory")}`} /></span> : <span className="nx-muted">{na}</span>}</td>
                          <td className="nx-num nx-mono">{nv.filter((v) => v.etat === "actif").length} / {nv.length}</td>
                          <td className="nx-mono">{formatUptimeLong(n.uptime_s, lang) || "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <h3 className="nx-subhead nx-caps">{t("ov.opsInProgress")}</h3>
              {ops.length === 0 ? <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("ov.noOps")}</p> : (
                <ul className="nx-oplist">
                  {ops.slice(0, 5).map((o) => (
                    <li key={o.id}>
                      <span>{taskLabel(o.type)}</span>
                      <span className="nx-progress nx-progress--indeterminate" role="progressbar" aria-label={taskLabel(o.type)}><span /></span>
                      <span className="nx-mono nx-muted">{o.cible || ""} · {opAge(o)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="nx-card" aria-labelledby="ov-alerts">
              <div className="nx-cardhead"><h2 id="ov-alerts">{t("nav.alerts")}</h2><span className="nx-muted nx-cardhead-note">{alerts.length ? t("ov.alertsActive", { n: alerts.length }) : ""}</span></div>
              {alerts.length === 0 ? <p role="status"><StatusIndicator override={{ key: "health.ok", shape: "dot", tone: "success" }} /> <span className="nx-muted">{t("ns.noIncident")}</span></p> : (
                <ul className="nx-alertlist">
                  {alerts.slice(0, 7).map((a) => {
                    const [key, tone] = sev(a);
                    return (
                      <li key={a.id}>
                        <StatusIndicator override={{ key, shape: tone === "danger" ? "diamond" : tone === "warning" ? "triangle" : "ring", tone }} />
                        <span className="nx-alert-text">{a.text}</span>
                        {a.target && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo(a.target.type, a.target.id, a.target.tab)}>{t("menu.open")}</button>}
                      </li>
                    );
                  })}
                </ul>
              )}
              {alerts.length > 0 && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => window.dispatchEvent(new CustomEvent("nx:dock", { detail: "alerts" }))}>{t("ns.viewAlerts")}</button>}
            </section>
          </div>

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
        </>
      )}

      {view === "perf" && (
        <>
          <div className="nx-cols nx-cols--even">{cpuCard}{memCard}</div>
          <section className="nx-card" aria-labelledby="ov-na">
            <div className="nx-cardhead"><h2 id="ov-na">{t("ov.notProvided")}</h2></div>
            <dl className="nx-dl">
              <dt>{t("ns.network")}</dt><dd className="nx-muted">{t("ns.networkNa")}</dd>
              <dt>{t("ov.iops")}</dt><dd className="nx-muted">{na}</dd>
              <dt>{t("ov.remoteLoad")}</dt><dd className="nx-muted">{na}</dd>
            </dl>
            {last?.mem_total_mb && <p className="nx-hint" style={{ marginBottom: 0 }}>{t("ns.memory")}: <span className="nx-mono">{formatSizeMb(last.mem_used_mb, lang)} / {formatSizeMb(last.mem_total_mb, lang)}</span></p>}
          </section>
        </>
      )}

      {view === "events" && (
        <section className="nx-card" aria-labelledby="ov-events">
          <div className="nx-cardhead"><h2 id="ov-events">{t("ns.activity")}</h2><button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("datacenter", null, "activity")}>{t("ns.allActivity")}</button></div>
          {recent == null ? <p className="nx-muted" role="status">{t("loading")}</p> : recent.length === 0 ? <p className="nx-muted" role="status">{t("dock.none")}</p> : (
            <ul className="nx-list">
              {recent.map((r) => <li key={r.id}><StatusIndicator kind="task" wire={r.statut} compact /><span>{taskLabel(r.type)} <span className="nx-mono nx-muted">{r.cible || ""}</span></span><span className="nx-mono nx-muted">{clockTime(r.debut_le || r.cree_le, lang)}</span></li>)}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
