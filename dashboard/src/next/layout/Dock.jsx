import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import StatusIndicator from "../components/StatusIndicator";
import { taskLabel } from "../lib/enums";
import { formatDuration } from "../lib/format";
import { deriveAlerts } from "../lib/alerts";

const ALERT_STATE = { "node-offline": "state.offline", "vm-crashed": "state.crashed", "vm-blocked": "state.blocked", "pool-state": "state.degraded", "pool-full": "state.degraded", "pool-high": "state.degraded", "task-failed": "state.failed" };

// Activity panel: a slide-over on the right (closed by default) instead of a permanent bottom band, so
// pages keep their whole height. Tasks of this session, audit-log hint, derived alerts.
export default function Dock() {
  const t = useT();
  useLangStore((s) => s.lang);
  const { tasks, collapsed, toggle, nodes, vms, storagePools, navigateTo } = useInfraStore(useShallow((s) => ({
    tasks: s.tasks, collapsed: s.taskLogCollapsed, toggle: s.toggleTaskLog, nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, navigateTo: s.navigateTo,
  })));
  const [tab, setTab] = useState("tasks");
  const [openId, setOpenId] = useState(null);
  const open = !collapsed;

  useEffect(() => {
    const on = (e) => { setTab(e.detail); if (useInfraStore.getState().taskLogCollapsed) useInfraStore.getState().toggleTaskLog(); };
    window.addEventListener("nx:dock", on);
    return () => window.removeEventListener("nx:dock", on);
  }, []);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") useInfraStore.getState().toggleTaskLog(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const running = tasks.filter((x) => x.statut === "en_cours").length;
  const alerts = deriveAlerts({ nodes, vms, storagePools, tasks });
  const tabs = [["tasks", t("dock.tasks"), running || null], ["logs", t("dock.logs"), null], ["alerts", t("dock.alerts"), alerts.length || null]];

  return (
    <aside className="nx-drawer" data-open={open} aria-label={t("dock.title")} inert={!open}>
      <header className="nx-drawer-head">
        <h2>{t("dock.title")}</h2>
        <span className="nx-muted" style={{ fontSize: "var(--font-size-xs)" }}>{running > 0 ? `${t("dock.running", { n: running })} · ` : ""}{t("dock.total", { n: tasks.length })}</span>
        <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" style={{ marginLeft: "auto" }} aria-label={t("dock.close")} onClick={toggle}>
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M2 2l10 10M12 2 2 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" fill="none" /></svg>
        </button>
      </header>
      <div className="nx-drawer-tabs" role="tablist" aria-label={t("dock.title")}>
        {tabs.map(([id, label, n]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
            {label}{n ? <span className="nx-badge-count" style={{ marginLeft: 6 }}>{n}</span> : null}
          </button>
        ))}
      </div>
      <div className="nx-drawer-body" role="tabpanel" aria-label={tabs.find((x) => x[0] === tab)[1]} tabIndex={0}>
        {tab === "tasks" && (tasks.length === 0 ? (
          <div className="nx-drawer-empty"><strong>{t("dock.none")}</strong><span className="nx-muted">{t("dock.emptyHelp")}</span></div>
        ) : tasks.map((x) => (
          <div key={x.id} className="nx-trow">
            <button type="button" className="nx-trow-main" aria-expanded={openId === x.id} onClick={() => setOpenId(openId === x.id ? null : x.id)}>
              <StatusIndicator kind="task" wire={x.statut} compact />
              <span className="nx-trow-text"><span>{taskLabel(x.type)}</span><span className="nx-mono nx-muted">{x.cible}</span></span>
              <span className="nx-mono nx-muted">{formatDuration(x.debut, x.fin)}</span>
            </button>
            {x.statut === "en_cours" && <span className="nx-progress" role="progressbar" aria-valuenow={x.progres || 0} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${x.progres || 0}%` }} /></span>}
            {openId === x.id && (
              <div className="nx-muted nx-trow-detail">
                {t("task.node")}: {x.node} · {t("task.user")}: {x.utilisateur} · {t("task.status")}: <StatusIndicator kind="task" wire={x.statut} />{x.erreur ? <span className="nx-mono" style={{ overflowWrap: "anywhere" }}> — {x.erreur}</span> : null}
              </div>
            )}
          </div>
        )))}
        {tab === "logs" && <div className="nx-drawer-empty"><span className="nx-muted">{t("dock.logsHint")}</span><button type="button" className="nx-btn" onClick={() => { navigateTo("datacenter", null, "journal"); toggle(); }}>{t("nav.systemLogs")}</button></div>}
        {tab === "alerts" && (alerts.length === 0 ? <div className="nx-drawer-empty" role="status"><StatusIndicator override={{ key: "health.ok", shape: "dot", tone: "success" }} /><span className="nx-muted">{t("dock.alertsNone")}</span></div> : alerts.map((a) => (
          <div key={a.id} className="nx-trow">
            <div className="nx-trow-main" style={{ cursor: "default" }}>
              <StatusIndicator override={{ key: ALERT_STATE[a.kind] || "state.unknown", shape: a.level === "danger" ? "diamond" : a.level === "warning" ? "triangle" : "ring", tone: a.level }} compact />
              <span className="nx-trow-text"><span className="nx-mono">{a.text}</span><span className="nx-muted">{a.kind.replace(/-/g, " ")}{a.detail ? `: ${a.detail}` : ""}</span></span>
              {a.target && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => { navigateTo(a.target.type, a.target.id, a.target.tab); toggle(); }}>{t("menu.open")}</button>}
            </div>
          </div>
        )))}
      </div>
    </aside>
  );
}
