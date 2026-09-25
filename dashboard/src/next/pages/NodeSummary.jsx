import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchHostMetricsHistory, fetchNodeCapabilitiesById, fetchTasks } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { capabilities, vmActionState } from "../lib/capabilities";
import { useVmActions } from "../lib/vmActions";
import { deriveAlerts } from "../lib/alerts";
import { taskLabel } from "../lib/enums";
import { formatSizeMb, formatSizeGb, formatUptimeLong, clockTime, formatVersionInt } from "../lib/format";
import KpiTile from "../components/KpiTile";
import StatusIndicator from "../components/StatusIndicator";

const VIEW_KEY = "hyperlite-next-vmview";
const AUTO_TABLE_FROM = 5; // cards are readable for a handful of VMs, a table scales past that

function readView() { try { return localStorage.getItem(VIEW_KEY) || "auto"; } catch { return "auto"; } }

function VmActions({ vm }) {
  const t = useT();
  const role = useAuthStore((s) => s.role);
  const caps = capabilities(role);
  const { run, openConsole } = useVmActions();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const running = vm.etat === "actif";
  const power = vmActionState(running ? "stop" : "start", vm, caps);
  const cons = vmActionState("console", vm, caps);
  return (
    <span style={{ display: "inline-flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
      <button type="button" className="nx-btn" aria-disabled={!cons.enabled || undefined} title={!cons.enabled ? t(cons.reason) : undefined} onClick={() => cons.enabled && openConsole(vm)}>{t("menu.console")}</button>
      <button type="button" className="nx-btn" aria-disabled={!power.enabled || undefined} title={!power.enabled ? t(power.reason) : undefined} onClick={() => power.enabled && run(vm, running ? "stop" : "start")}>{running ? t("menu.stop") : t("menu.start")}</button>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("vm", vm.nom, "summary")}>{t("menu.open")}</button>
    </span>
  );
}

export default function NodeSummary({ resource: node }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { vms, storagePools, navigateTo } = useInfraStore(useShallow((s) => ({ vms: s.vms, storagePools: s.storagePools, navigateTo: s.navigateTo })));
  const role = useAuthStore((s) => s.role);
  const caps = capabilities(role);
  const [rows, setRows] = useState([]);
  const [hostCaps, setHostCaps] = useState(null);
  const [recent, setRecent] = useState(null);
  const [view, setViewState] = useState(readView);
  const [q, setQ] = useState("");
  const isLocal = node?.id === "local";

  const setView = (v) => { setViewState(v); try { localStorage.setItem(VIEW_KEY, v); } catch { /* preference only */ } };

  usePolling(async () => { if (isLocal) setRows(await fetchHostMetricsHistory("1h")); }, 15000, { enabled: !!isLocal });
  useEffect(() => { if (isLocal) fetchHostMetricsHistory("1h").then(setRows).catch(() => {}); }, [isLocal]);
  useEffect(() => { if (node) fetchNodeCapabilitiesById(node.id).then(setHostCaps).catch(() => setHostCaps(null)); }, [node]);
  usePolling(async () => { setRecent(await fetchTasks({ limit: 8, tri: "cree_le", ordre: "desc" })); }, 10000);
  useEffect(() => { fetchTasks({ limit: 8, tri: "cree_le", ordre: "desc" }).then(setRecent).catch(() => setRecent([])); }, []);

  const nodeVms = useMemo(() => vms.filter((v) => v.node === node?.id), [vms, node]);
  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    return n ? nodeVms.filter((v) => `${v.nom} ${v.ip || ""} ${v.os || ""}`.toLowerCase().includes(n)) : nodeVms;
  }, [nodeVms, q]);
  if (!node) return null;

  const mode = view === "auto" ? (nodeVms.length >= AUTO_TABLE_FROM ? "table" : "cards") : view;
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
        <section className="nx-card" aria-labelledby="ns-vms">
          <div className="nx-cardhead">
            <h2 id="ns-vms">{t("ns.vms")} <span className="nx-count">{nodeVms.length}</span></h2>
            <input className="nx-input" type="search" aria-label={t("ns.filterVms")} placeholder={t("ns.filterVms")} value={q} onChange={(e) => setQ(e.target.value)} />
            <div className="nx-seg" role="group" aria-label={t("ns.viewMode")}>
              <button type="button" aria-pressed={mode === "cards"} onClick={() => setView("cards")}>{t("ns.cards")}</button>
              <button type="button" aria-pressed={mode === "table"} onClick={() => setView("table")}>{t("ns.table")}</button>
            </div>
          </div>
          {nodeVms.length === 0 ? <p className="nx-muted" role="status">{t("ns.noVms")}</p>
            : shown.length === 0 ? <p className="nx-muted" role="status">{t("find.none", { q })}</p>
            : mode === "cards" ? (
              <div className="nx-cards">
                {shown.map((vm) => (
                  <article key={vm.nom} className="nx-vmcard" aria-label={vm.nom}>
                    <header><button type="button" className="nx-link" onClick={() => navigateTo("vm", vm.nom, "summary")}>{vm.nom}</button><StatusIndicator kind="vm" wire={vm.etat} />{vm.uptime_s ? <span className="nx-muted"> · {formatUptimeLong(vm.uptime_s, lang)}</span> : null}</header>
                    <p className="nx-mono nx-muted">{t("ns.vmSpec", { cpu: vm.vcpu, ram: formatSizeMb(vm.memoire_mo, lang) })} · {vm.ip ? vm.ip : <span title={t("ns.ipHelp")}>{t("ns.ipNa")} ⓘ</span>}</p>
                    <VmActions vm={vm} />
                  </article>
                ))}
              </div>
            ) : (
              <div className="nx-tablewrap">
                <table className="nx-table">
                  <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ns.col.name")}</th><th scope="col" className="nx-num">vCPU</th><th scope="col" className="nx-num">{t("ns.memory")}</th><th scope="col">IP</th><th scope="col">{t("ns.col.uptime")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
                  <tbody>
                    {shown.map((vm) => (
                      <tr key={vm.nom}>
                        <td><StatusIndicator kind="vm" wire={vm.etat} /></td>
                        <th scope="row"><button type="button" className="nx-link" onClick={() => navigateTo("vm", vm.nom, "summary")}>{vm.nom}</button></th>
                        <td className="nx-num nx-mono">{vm.vcpu}</td><td className="nx-num nx-mono">{formatSizeMb(vm.memoire_mo, lang)}</td>
                        <td className="nx-mono">{vm.ip || <span className="nx-muted" title={t("ns.ipHelp")}>{t("ns.ipNa")} ⓘ</span>}</td>
                        <td className="nx-mono">{formatUptimeLong(vm.uptime_s, lang) || "—"}</td>
                        <td><VmActions vm={vm} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </section>

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
