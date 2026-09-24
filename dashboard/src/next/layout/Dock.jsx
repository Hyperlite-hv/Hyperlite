import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import StatusIndicator from "../components/StatusIndicator";
import { taskLabel } from "../lib/enums";
import { formatDuration } from "../lib/format";
import { deriveAlerts } from "../lib/alerts";

const ALERT_STATE = { "node-offline": "state.offline", "vm-crashed": "state.crashed", "vm-blocked": "state.blocked", "pool-state": "state.degraded", "pool-full": "state.degraded", "pool-high": "state.degraded", "task-failed": "state.failed" };

// Bottom dock: session tasks (same list as the legacy task log), audit logs hint, derived alerts.
export default function Dock() {
  const t = useT();
  useLangStore((s) => s.lang);
  const { tasks, collapsed, toggle, nodes, vms, storagePools, navigateTo } = useInfraStore(useShallow((s) => ({
    tasks: s.tasks, collapsed: s.taskLogCollapsed, toggle: s.toggleTaskLog, nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, navigateTo: s.navigateTo,
  })));
  const [tab, setTab] = useState("tasks");
  const [openId, setOpenId] = useState(null);
  const running = tasks.filter((x) => x.statut === "en_cours").length;
  const alerts = deriveAlerts({ nodes, vms, storagePools, tasks });
  const open = !collapsed;
  const tabs = [["tasks", t("dock.tasks"), running || null], ["logs", t("dock.logs"), null], ["alerts", t("dock.alerts"), alerts.length || null]];

  return (
    <section className="nx-dock" data-open={open} aria-label={t("dock.tasks")}>
      <div className="nx-dock-bar">
        <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-expanded={open} aria-label={open ? t("dock.collapse") : t("dock.expand")} onClick={toggle}>
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style={{ transform: open ? "rotate(90deg)" : "rotate(-90deg)" }}><path d="M3 1.5 8 6 3 10.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
        </button>
        <div role="tablist" aria-label={t("dock.tasks")} style={{ display: "flex" }}>
          {tabs.map(([id, label, n]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => { setTab(id); if (!open) toggle(); }}>
              {label}{n ? <span className="nx-badge-count" style={{ marginLeft: 6 }}>{n}</span> : null}
            </button>
          ))}
        </div>
        <span className="nx-muted" style={{ marginLeft: "auto", fontSize: "var(--font-size-xs)" }}>
          {running > 0 ? t("dock.running", { n: running }) + " · " : ""}{t("dock.total", { n: tasks.length })}
        </span>
      </div>
      {open && (
        <div className="nx-dock-body" role="region" aria-label={tabs.find((x) => x[0] === tab)[1]} tabIndex={0}>
          {tab === "tasks" && (tasks.length === 0 ? <p className="nx-muted">{t("dock.none")}</p> : tasks.map((x) => (
            <div key={x.id}>
              <div className="nx-task" role="button" tabIndex={0} onClick={() => setOpenId(openId === x.id ? null : x.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpenId(openId === x.id ? null : x.id); } }} aria-expanded={openId === x.id}>
                <StatusIndicator kind="task" wire={x.statut} compact />
                <span>{taskLabel(x.type)}</span>
                <span className="nx-mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{x.cible}</span>
                {x.statut === "en_cours" ? <span className="nx-progress" role="progressbar" aria-valuenow={x.progres || 0} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${x.progres || 0}%` }} /></span> : <span />}
                <span className="nx-mono nx-muted" style={{ textAlign: "right" }}>{formatDuration(x.debut, x.fin)}</span>
              </div>
              {openId === x.id && (
                <div className="nx-muted" style={{ padding: "0 var(--space-2) var(--space-2) 2.5rem", fontSize: "var(--font-size-xs)" }}>
                  {t("task.node")}: {x.node} · {t("task.user")}: {x.utilisateur} · {t("task.status")}: <StatusIndicator kind="task" wire={x.statut} />{x.erreur ? <span className="nx-mono" style={{ overflowWrap: "anywhere" }}> — {x.erreur}</span> : null}
                </div>
              )}
            </div>
          )))}
          {tab === "logs" && <p className="nx-muted">{t("dock.logsHint")}</p>}
          {tab === "alerts" && (alerts.length === 0 ? <p className="nx-muted" role="status">{t("dock.alertsNone")}</p> : alerts.map((a) => (
            <div key={a.id} className="nx-task" style={{ gridTemplateColumns: "1.5rem minmax(0,1fr) auto" }}>
              <StatusIndicator override={{ key: ALERT_STATE[a.kind] || "state.unknown", shape: a.level === "danger" ? "diamond" : a.level === "warning" ? "triangle" : "ring", tone: a.level }} compact />
              <span><span className="nx-mono">{a.text}</span> <span className="nx-muted">— {a.kind.replace(/-/g, " ")}{a.detail ? `: ${a.detail}` : ""}</span></span>
              {a.target && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo(a.target.type, a.target.id, a.target.tab)}>{t("menu.open")}</button>}
            </div>
          )))}
        </div>
      )}
    </section>
  );
}
