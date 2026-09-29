import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchTasks, fetchTaskDetail, downloadTasksCsv } from "../../api/client";
import TaskLog from "../components/TaskLog";
import { parseVmKey } from "../lib/vmId";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { taskLabel, TASK_LABEL_KEYS } from "../lib/enums";
import { errorMessage, normalizeDetail } from "../lib/errors";
import { ErrorState } from "../components/States";
import { PageHeader, Empty, Loading, TableWrap, StatePill } from "../components/ui";
import { Download, ListChecks, Search } from "lucide-react";

const SINCE = { all: null, "1h": 3600, "24h": 86400, "7d": 604800 };
const STUCK_S = 900; // a task "running" for more than 15 minutes is flagged (no cleanup after a crash)
const RUNNING = ["en_cours", "en_attente"];
// The status tabs filter the loaded tasks in the page, so that their counts stay right whichever tab is open.
const VIEWS = { all: () => true, running: (r) => RUNNING.includes(r.statut), failed: (r) => r.statut === "echec" };

function duration(start, end, now) {
  if (!start) return "—";
  const s = Math.max(0, Math.round(((end ? new Date(end).getTime() : now) - new Date(start).getTime()) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")}`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}

// Which task the journal shows when nothing was picked: the first running one, else the last failure, else the latest.
function defaultTask(rows) {
  return rows.find((r) => RUNNING.includes(r.statut)) || rows.find((r) => r.statut === "echec") || rows[0] || null;
}

// Tasks: every persisted task with the filters the API supports (type, target, node, user, period), status tabs,
// live refresh, and the journal of the selected task under the list, with a CSV export.
// On a node page (`selection.type === "node"`) the same view is scoped to that node.
export default function ActivityPage({ selection }) {
  const nodeId = selection?.type === "node" ? selection.id : undefined;
  // A VM's or a container's own history: its exact name, among the tasks of its kind.
  const own = selection?.type === "vm" ? { objet: parseVmKey(selection.id).nom, famille: "vm" }
    : selection?.type === "container" ? { objet: selection.id, famille: "container" } : null;
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const vms = useInfraStore((s) => s.vms);
  const nodes = useInfraStore((s) => s.nodes);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const EMPTY_F = { type: "", cible: "", username: "", node: "", since: "24h" };
  const [f, setF] = useState(EMPTY_F);
  const [view, setView] = useState("all");
  const [now, setNow] = useState(() => Date.now());
  const [picked, setPicked] = useState(null);
  const [detail, setDetail] = useState({});

  const [exporting, setExporting] = useState(false);
  const pushToast = useInfraStore((s) => s.pushToast);
  const filters = useCallback(() => {
    const depuis = SINCE[f.since] ? new Date(Date.now() - SINCE[f.since] * 1000).toISOString() : undefined;
    return { node: nodeId ?? (f.node || undefined), type: f.type, cible: f.cible, username: f.username, depuis, ...own };
  }, [f, nodeId, own?.objet, own?.famille]); // eslint-disable-line react-hooks/exhaustive-deps
  const load = useCallback(async () => {
    try {
      const r = await fetchTasks({ ...filters(), limit: 200, tri: "cree_le", ordre: "desc" });
      setRows(Array.isArray(r) ? r : []); setNow(Date.now()); setError(null);
    } catch (e) { setError(normalizeDetail(e.message)); }
  }, [filters]);
  usePolling(load, 8000);
  useEffect(() => { load(); }, [load]);

  const counts = useMemo(() => ({
    running: (rows || []).filter(VIEWS.running).length,
    failed: (rows || []).filter(VIEWS.failed).length,
  }), [rows]);
  const shown = useMemo(() => (rows || []).filter(VIEWS[view]), [rows, view]);
  const current = shown.find((r) => r.id === picked) || defaultTask(shown);

  // The audit entries of the task's target, fetched once per task.
  useEffect(() => {
    if (!current || detail[current.id]) return;
    let live = true;
    fetchTaskDetail(current.id).then((d) => d, () => ({ error: true })).then((d) => { if (live) setDetail((x) => ({ ...x, [current.id]: d })); });
    return () => { live = false; };
  }, [current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const users = useMemo(() => [...new Set((rows || []).map((r) => r.username).filter(Boolean))], [rows]);
  const upd = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const today = new Date(now).toDateString();
  const fmt = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    const opts = d.toDateString() === today ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
      : { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" };
    return new Intl.DateTimeFormat(lang, opts).format(d);
  };
  // Every matching task, from the server: the page itself only holds the 200 most recent.
  const download = async () => {
    setExporting(true);
    try { await downloadTasksCsv({ ...filters(), statut: view === "failed" ? "echec" : view === "running" ? "en_cours" : "" }); }
    catch (e) { pushToast({ kind: "error", title: t("act.exportFailed"), message: errorMessage(e) }); }
    finally { setExporting(false); }
  };

  const dirty = JSON.stringify(f) !== JSON.stringify(EMPTY_F) || view !== "all";
  const nodeName = (id) => nodes.find((n) => n.id === id || n.nom === id)?.nom || id;
  const period = t(`act.over.${f.since}`);

  const tabs = (
    <div className="nx-seg2" role="group" aria-label={t("task.status")}>
      {[["all", t("act.view.all")], ["running", `${t("act.view.running")} · ${counts.running}`], ["failed", `${t("act.view.failed")} · ${counts.failed}`]].map(([k, label]) => (
        <button key={k} type="button" aria-pressed={view === k} onClick={() => setView(k)}>{label}</button>
      ))}
    </div>
  );

  const table = error ? <ErrorState message={error} onRetry={load} />
    : rows == null ? <Loading style={{ padding: "var(--space-4)" }} />
    : shown.length === 0 ? <Empty icon={ListChecks} title={t("act.none")} text={dirty ? t("act.noneFiltered") : t("act.noneHelp")} /> : (
      <TableWrap>
        <table className="nx-table nx-tasks">
          <thead><tr><th scope="col">{t("act.task")}</th><th scope="col">{t("act.target")}</th><th scope="col">{t("ns.node")}</th><th scope="col">{t("task.status")}</th><th scope="col">{t("act.started")}</th><th scope="col">{t("act.duration")}</th></tr></thead>
          <tbody>
            {shown.map((r) => {
              const stuck = r.statut === "en_cours" && (now - new Date(r.debut_le || r.cree_le).getTime()) / 1000 > STUCK_S;
              const isVm = vms.some((v) => v.nom === r.cible);
              const selected = current?.id === r.id;
              const pct = Math.max(0, Math.min(100, Number(r.progres) || 0));
              return (
                <tr key={r.id} className={`nx-rowlink${selected ? " is-selected" : ""}`} aria-selected={selected} onClick={() => setPicked(r.id)}>
                  <th scope="row"><button type="button" className="nx-lnk nx-tasks-name" aria-controls="nx-task-journal" aria-pressed={selected} onClick={(e) => { e.stopPropagation(); setPicked(r.id); }}>{taskLabel(r.type)}</button>{stuck && <span className="nx-tone-warning" title={t("act.stuckHelp")}> ▲ {t("act.stuck")}</span>}</th>
                  <td>{isVm ? <button type="button" className="nx-lnk nx-mono" onClick={(e) => { e.stopPropagation(); navigateTo("vm", r.cible, "summary"); }}>{r.cible}</button> : <span className="nx-mono">{r.cible || "—"}</span>}</td>
                  <td className="nx-mono nx-muted">{r.node ? nodeName(r.node) : "—"}</td>
                  <td>
                    <span className="nx-tasks-state">
                      <StatePill kind="task" wire={r.statut} />
                      {r.statut === "en_cours" && pct > 0 && pct < 100 && (
                        <span className="nx-tasks-bar" role="progressbar" aria-label={t("act.progress")} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${pct}%` }} /></span>
                      )}
                    </span>
                  </td>
                  <td className="nx-mono nx-muted">{fmt(r.debut_le || r.cree_le)}</td>
                  <td className="nx-mono">{r.debut_le ? duration(r.debut_le, r.fin_le, now) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableWrap>
    );

  const audit = current ? (detail[current.id]?.logs || []) : [];
  const journal = current && (
    <section className="nx-card2 nx-journal" id="nx-task-journal" aria-labelledby="nx-task-journal-h">
      <header className="nx-journal-h">
        <h2 id="nx-task-journal-h">{t("act.journal")} · {taskLabel(current.type)}{current.cible ? ` ${current.cible}` : ""}</h2>
        <StatePill kind="task" wire={current.statut} />
        <span className="nx-sp" />
        <span className="nx-mono nx-muted nx-journal-id" title={t("act.taskId")}>{current.username ? `${current.username} · ` : ""}{current.id}</span>
      </header>
      <TaskLog key={current.id} task={current} onChanged={load} terminal />
      {audit.length > 0 && (
        <details className="nx-journal-audit">
          <summary>{t("tl.audit")} ({Math.min(audit.length, 8)})</summary>
          {audit.slice(0, 8).map((l, i) => <p key={i} className="nx-mono nx-muted">{l.timestamp} · {l.action} · {l.result}{l.error_message ? ` · ${l.error_message}` : ""}</p>)}
        </details>
      )}
    </section>
  );

  const header = !nodeId && (
    <PageHeader
      title={t("act.title")}
      desc={rows ? t("act.summary", { run: counts.running, fail: counts.failed, period }) : null}
      actions={tabs}
    />
  );

  return (
    <>
      {header}
      <div className="nx-bar" role="group" aria-label={t("act.filters")}>
        {nodeId && tabs}
        <label className="nx-search2"><Search size={15} aria-hidden="true" /><input type="search" aria-label={t("act.target")} value={f.cible} onChange={upd("cible")} placeholder={t("act.searchPh")} /></label>
        <select className="nx-sel" aria-label={t("act.type")} value={f.type} onChange={upd("type")}><option value="">{t("act.allTypes")}</option>{Object.keys(TASK_LABEL_KEYS).map((k) => <option key={k} value={k}>{taskLabel(k)}</option>)}</select>
        {!nodeId && nodes.length > 1 && <select className="nx-sel" aria-label={t("ns.node")} value={f.node} onChange={upd("node")}><option value="">{t("act.allNodes")}</option>{nodes.map((n) => <option key={n.id} value={n.id === "local" ? n.nom : n.id}>{n.nom}</option>)}</select>}
        <select className="nx-sel" aria-label={t("task.user")} value={f.username} onChange={upd("username")}><option value="">{t("act.allUsers")}</option>{users.map((u) => <option key={u} value={u}>{u}</option>)}</select>
        <select className="nx-sel" aria-label={t("act.period")} value={f.since} onChange={upd("since")}>{Object.keys(SINCE).map((k) => <option key={k} value={k}>{t(`act.since.${k}`)}</option>)}</select>
        {dirty && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => { setF(EMPTY_F); setView("all"); }}>{t("act.clear")}</button>}
        <span className="nx-sp" />
        <button type="button" className="nx-btn" disabled={!rows?.length || exporting} onClick={download}><Download size={15} aria-hidden="true" />{t("act.export")}</button>
      </div>
      <div className="nx-card2 nx-card2--flush">{table}</div>
      {journal}
    </>
  );
}
ActivityPage.ownHeader = true;
