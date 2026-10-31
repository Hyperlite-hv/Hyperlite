import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchClusterTasks, fetchTaskDetail, downloadTasksCsv } from "../../api/client";
import TaskLog from "../components/TaskLog";
import { parseVmKey } from "../lib/vmId";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { listOf } from "../lib/clusterList";
import { taskLabel, TASK_LABEL_KEYS } from "../lib/enums";
import { errorMessage, normalizeDetail } from "../lib/errors";
import { localizeApiText } from "../lib/apiText";
import {
  accounts, byDay, durationText, foldRuns, isRunning, readHidden, readSeen, remaining, runTargets, seconds, sinceText,
  writeHidden, writeSeen,
} from "../lib/activity";
import { ErrorState } from "../components/States";
import { PageHeader, Empty, Loading, StatePill } from "../components/ui";
import { ChevronDown, ChevronRight, Copy, Download, Eye, EyeOff, ListChecks, Search, TriangleAlert } from "lucide-react";

const SINCE = { all: null, "1h": 3600, "24h": 86400, "7d": 604800 };
const STUCK_S = 900; // a task "running" for more than 15 minutes is flagged (no cleanup after a crash)
const PAGE = 200; // tasks asked for at a time; "Show older" asks for one page more
const MAX_ROWS = 1000;
const ATTENTION_SHOWN = 3;
// The status filter keeps the loaded tasks in the page, so that its counts stay right whichever is chosen.
const VIEWS = { all: () => true, running: isRunning, failed: (r) => r.status === "failed" };

// Tasks, for someone who wants to know what happened and why: what runs now (progress, time left), the failures not
// looked at yet (their reason, the way to their log), then the history in days, an account's close tasks folded into
// one run (an image factory's build is one line, not six). Every task opens its full log beside the list, which is
// the point of the page when something went wrong. On a node, a VM or a container page, the same view is scoped to it.
// embedded: the Tasks tab of the Activity page (ActivityHub.jsx), under its header.
export default function ActivityPage({ selection, embedded = false }) {
  const nodeId = selection?.type === "node" ? selection.id : undefined;
  // A VM's or a container's own history: its exact name, among the tasks of its kind.
  const own = selection?.type === "vm" ? { object: parseVmKey(selection.id).name, family: "vm" }
    : selection?.type === "container" ? { object: selection.id, family: "container" } : null;
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const me = useAuthStore((s) => s.username);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const pushToast = useInfraStore((s) => s.pushToast);
  const vms = useInfraStore((s) => s.vms);
  const nodes = useInfraStore((s) => s.nodes);
  const [rows, setRows] = useState(null);
  const [unreachable, setUnreachable] = useState([]);
  const [error, setError] = useState(null);
  const EMPTY_F = { type: "", target: "", node: "", since: "24h" };
  const [f, setF] = useState(EMPTY_F);
  const [view, setView] = useState("all");
  const [limit, setLimit] = useState(PAGE);
  const [now, setNow] = useState(() => Date.now());
  const [picked, setPicked] = useState(null);
  const [detail, setDetail] = useState({});
  const [open, setOpen] = useState(() => new Set()); // runs and earlier days unfolded
  const [seen, setSeen] = useState(readSeen);
  const [hidden, setHidden] = useState(readHidden);
  const [exporting, setExporting] = useState(false);

  const filters = useCallback(() => {
    const since = SINCE[f.since] ? new Date(Date.now() - SINCE[f.since] * 1000).toISOString() : undefined;
    return { node: nodeId ?? (f.node || undefined), type: f.type, target: f.target, since, ...own };
  }, [f, nodeId, own?.object, own?.family]); // eslint-disable-line react-hooks/exhaustive-deps
  const load = useCallback(async () => {
    try {
      // In a cluster, every member's tasks (each with the node that holds it) and the members that did not answer.
      const r = await fetchClusterTasks({ ...filters(), limit, sort: "created_at", position: "desc" });
      setRows(listOf(r)); setUnreachable(Array.isArray(r?.unreachable) ? r.unreachable : []);
      setNow(Date.now()); setError(null);
    } catch (e) { setError(normalizeDetail(e.message)); }
  }, [filters, limit]);
  usePolling(load, 8000);
  useEffect(() => { load(); }, [load]);

  const all = useMemo(() => rows || [], [rows]);
  const visible = useMemo(() => all.filter((r) => !hidden.has(r.username || "system")), [all, hidden]);
  const counts = useMemo(() => ({ running: visible.filter(VIEWS.running).length, failed: visible.filter(VIEWS.failed).length }), [visible]);
  const shown = useMemo(() => visible.filter(VIEWS[view]), [visible, view]);
  const days = useMemo(() => byDay(foldRuns(shown), now), [shown, now]);
  const runningNow = useMemo(() => all.filter(isRunning), [all]);
  const attention = useMemo(() => all.filter((r) => r.status === "failed" && !seen.has(r.id)), [all, seen]);
  const current = all.find((r) => r.id === picked) || runningNow[0] || attention[0] || shown[0] || null;

  // The audit entries of the task's target, fetched once per task.
  useEffect(() => {
    if (!current || detail[current.id]) return;
    let live = true;
    fetchTaskDetail(current.id, current.on).then((d) => d, () => ({ error: true })).then((d) => { if (live) setDetail((x) => ({ ...x, [current.id]: d })); });
    return () => { live = false; };
  }, [current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const upd = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const toggle = (key) => setOpen((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n; });
  const markSeen = (ids) => setSeen((s) => { const n = new Set(s); ids.forEach((id) => n.add(id)); writeSeen(n); return n; });
  const flipAccount = (u) => setHidden((s) => { const n = new Set(s); if (n.has(u)) n.delete(u); else n.add(u); writeHidden(n); return n; });
  const showAccounts = () => { const n = new Set(); writeHidden(n); setHidden(n); };
  const onlyMine = () => { const n = new Set(accounts(all).map((a) => a.username).filter((u) => u !== me)); writeHidden(n); setHidden(n); };
  const nodeName = (id) => nodes.find((n) => n.id === id || n.name === id)?.name || id;
  const isVm = (name) => vms.some((v) => v.name === name);
  const absolute = (iso) => (iso ? new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(iso)) : "—");
  const what = (r) => `${taskLabel(r.type)}${r.target ? ` · ${r.target}` : ""}`;
  const pick = (id) => { setPicked(id); requestAnimationFrame(() => document.getElementById("nx-task-journal")?.scrollIntoView?.({ block: "nearest" })); };

  // Every matching task, from the server: the page itself only holds the most recent.
  const download = async () => {
    setExporting(true);
    try {
      const { unreachable: missed } = await downloadTasksCsv({ ...filters(), status: view === "failed" ? "failed" : view === "running" ? "running" : "" });
      if (missed.length) pushToast({ kind: "error", title: t("act.exportPartial"), message: t("act.exportMissed", { nodes: missed.join(", ") }) });
    }
    catch (e) { pushToast({ kind: "error", title: t("act.exportFailed"), message: errorMessage(e) }); }
    finally { setExporting(false); }
  };

  const dirty = JSON.stringify(f) !== JSON.stringify(EMPTY_F) || view !== "all";
  const period = t(`act.over.${f.since}`);

  // ---- What runs now ----
  const runningSection = runningNow.length > 0 && (
    <section className="nx-act-sec" aria-labelledby="nx-act-running">
      <h2 id="nx-act-running" className="nx-act-h">{t("act.runningNow")} <span className="nx-muted">{runningNow.length}</span></h2>
      <div className="nx-act-cards">
        {runningNow.map((r) => {
          const pct = Math.max(0, Math.min(100, Number(r.progress) || 0));
          const elapsed = (now - new Date(r.started_at || r.created_at).getTime()) / 1000;
          const left = remaining(r, all, now);
          const stuck = elapsed > STUCK_S;
          return (
            <article key={r.id} className={`nx-act-card${current?.id === r.id ? " is-selected" : ""}`}>
              <div className="nx-act-card-h">
                <button type="button" className="nx-lnk" onClick={() => pick(r.id)}>{what(r)}</button>
                <StatePill kind="task" wire={r.status} />
              </div>
              <div className="nx-act-bar" role="progressbar" aria-label={t("act.progress")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct || undefined}>
                <span style={{ width: `${pct || 8}%` }} data-indeterminate={pct ? undefined : "true"} />
              </div>
              <p className="nx-act-meta">
                {[r.username && t("act.by", { user: r.username }), r.node && nodeName(r.node), t("act.elapsed", { d: durationText(elapsed, lang) })].filter(Boolean).join(" · ")}
                {" · "}
                <span className={stuck ? "nx-tone-warning" : undefined} title={stuck ? t("act.stuckHelp") : undefined}>
                  {stuck ? t("act.stuck") : left == null ? (pct ? `${pct} %` : t("act.noEstimate")) : left === 0 ? t("act.longer") : t("act.eta", { d: durationText(left, lang) })}
                </span>
              </p>
            </article>
          );
        })}
      </div>
    </section>
  );

  // ---- The failures not looked at yet ----
  const attentionSection = attention.length > 0 && view !== "failed" && (
    <section className="nx-act-sec" aria-labelledby="nx-act-attention">
      <div className="nx-act-h-row">
        <h2 id="nx-act-attention" className="nx-act-h">{t("act.attention")} <span className="nx-muted">{attention.length}</span></h2>
        <span className="nx-sp" />
        {attention.length > 1 && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => markSeen(attention.map((r) => r.id))}>{t("act.markAllSeen")}</button>}
      </div>
      <ul className="nx-act-alerts">
        {attention.slice(0, ATTENTION_SHOWN).map((r) => (
          <li key={r.id} className="nx-bn" data-tone="danger">
            <TriangleAlert size={16} aria-hidden="true" />
            <div className="nx-act-alert-main">
              <p className="nx-act-alert-t"><b>{what(r)}</b> <span className="nx-muted">· {sinceText(r.finished_at || r.started_at, lang, now)}{r.node ? ` · ${nodeName(r.node)}` : ""}{r.username ? ` · ${r.username}` : ""}</span></p>
              {r.error && <p className="nx-act-alert-e">{localizeApiText(t, r.error)}</p>}
            </div>
            <div className="nx-act-alert-acts">
              <button type="button" className="nx-btn nx-btn--sm" onClick={() => pick(r.id)}>{t("act.showLog")}</button>
              {isVm(r.target) && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => navigateTo("vm", r.target, "summary")}>{t("act.openVm")}</button>}
              <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => markSeen([r.id])}>{t("act.markSeen")}</button>
            </div>
          </li>
        ))}
      </ul>
      {attention.length > ATTENTION_SHOWN && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setView("failed")}>{t("act.moreFailures", { n: attention.length - ATTENTION_SHOWN })}</button>}
    </section>
  );

  // ---- The history ----
  const row = (r, nested = false) => {
    const stuck = isRunning(r) && (now - new Date(r.started_at || r.created_at).getTime()) / 1000 > STUCK_S;
    const selected = current?.id === r.id;
    return (
      <li key={r.id} className={`nx-act-row${nested ? " nx-act-row--nested" : ""}${selected ? " is-selected" : ""}`}>
        <button type="button" className="nx-act-rowbtn" aria-pressed={selected} aria-controls="nx-task-journal" onClick={() => pick(r.id)}>
          <span className="nx-act-what"><b>{taskLabel(r.type)}</b>{r.target && <span className="nx-act-target">{r.target}</span>}{stuck && <span className="nx-tone-warning"> · {t("act.stuck")}</span>}</span>
          <span className="nx-act-who">{[r.username, r.node && nodeName(r.node)].filter(Boolean).join(" · ")}</span>
          <StatePill kind="task" wire={r.status} />
          <span className="nx-act-when" title={absolute(r.started_at || r.created_at)}>{sinceText(r.started_at || r.created_at, lang, now)}</span>
          <span className="nx-act-dur">{isRunning(r) ? "…" : durationText(seconds(r), lang)}</span>
        </button>
      </li>
    );
  };
  const runRow = (item) => {
    const expanded = open.has(item.id);
    const first = item.tasks[item.tasks.length - 1];
    const last = item.tasks[0];
    const span = last.finished_at && first.started_at ? (new Date(last.finished_at) - new Date(first.started_at)) / 1000 : null;
    const targets = runTargets(item.tasks);
    return (
      <li key={item.id} className="nx-act-run">
        <button type="button" className="nx-act-rowbtn" aria-expanded={expanded} onClick={() => toggle(item.id)}>
          <span className="nx-act-what">{expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}<b>{t("act.runOf", { user: item.username || "system", n: item.tasks.length })}</b>{targets.length > 0 && <span className="nx-act-target">{targets.join(", ")}</span>}</span>
          <span className="nx-act-who">{[...new Set(item.tasks.map((x) => taskLabel(x.type)))].slice(0, 3).join(", ")}</span>
          <StatePill kind="task" wire={item.status} />
          <span className="nx-act-when" title={absolute(first.started_at || first.created_at)}>{sinceText(first.started_at || first.created_at, lang, now)}</span>
          <span className="nx-act-dur">{span == null ? "…" : durationText(span, lang)}</span>
        </button>
        {expanded && <ul className="nx-act-list">{item.tasks.map((r) => row(r, true))}</ul>}
      </li>
    );
  };
  const dayTitle = (d) => (d.day === "today" ? t("act.today") : d.day === "yesterday" ? t("act.yesterday")
    : new Intl.DateTimeFormat(lang, { weekday: "long", day: "numeric", month: "long" }).format(new Date(d.day)));
  const history = error ? <ErrorState message={error} onRetry={load} />
    : rows == null ? <Loading style={{ padding: "var(--space-4)" }} />
    : shown.length === 0 ? <div className="nx-card2"><Empty icon={ListChecks} title={t("act.none")} text={dirty || hidden.size ? t("act.noneFiltered") : t("act.noneHelp")} /></div> : (
      <div className="nx-act-days">
        {days.map((d, i) => {
          // The latest day is open; earlier ones fold into their counts until asked for, unless a filter narrowed them.
          const folded = i > 0 && view === "all" && !open.has(`day-${d.key}`);
          return (
            <section key={d.key} className="nx-act-day" aria-label={dayTitle(d)}>
              <button type="button" className="nx-act-dayh" aria-expanded={!folded} onClick={() => i > 0 && toggle(`day-${d.key}`)} disabled={i === 0}>
                {i > 0 && (folded ? <ChevronRight size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />)}
                <span className="nx-act-dayt">{dayTitle(d)}</span>
                <span className="nx-muted">{t("act.dayCount", { n: d.total })}{d.failed ? ` · ${t("act.dayFailed", { n: d.failed })}` : ""}</span>
              </button>
              {!folded && <ul className="nx-act-list nx-card2">{d.items.map((item) => (item.kind === "run" ? runRow(item) : row(item.task)))}</ul>}
            </section>
          );
        })}
        {rows.length >= limit && limit < MAX_ROWS && <button type="button" className="nx-btn nx-act-more" onClick={() => setLimit((l) => Math.min(MAX_ROWS, l + PAGE))}>{t("act.loadMore")}</button>}
      </div>
    );

  // ---- The selected task: everything there is to know when it went wrong ----
  const audit = current ? (detail[current.id]?.logs || []) : [];
  const copyId = async () => {
    try { await navigator.clipboard.writeText(current.id); pushToast({ kind: "success", title: t("tl.copied") }); }
    catch { pushToast({ kind: "error", title: t("tl.copyFailed") }); }
  };
  const journal = (
    <section className="nx-card2 nx-journal" id="nx-task-journal" aria-labelledby="nx-task-journal-h">
      {!current ? <p className="nx-muted nx-act-pick">{t("act.pick")}</p> : (
        <>
          <header className="nx-journal-h">
            <h2 id="nx-task-journal-h">{what(current)}</h2>
            <StatePill kind="task" wire={current.status} />
          </header>
          <dl className="nx-dl2 nx-act-facts">
            <dt>{t("act.account")}</dt><dd>{current.username || "system"}</dd>
            <dt>{t("ns.node")}</dt><dd>{current.node ? nodeName(current.node) : "—"}</dd>
            <dt>{t("act.started")}</dt><dd>{absolute(current.started_at || current.created_at)}</dd>
            <dt>{t("act.ended")}</dt><dd>{isRunning(current) ? t("act.notYet") : absolute(current.finished_at)}</dd>
            <dt>{t("act.duration")}</dt><dd>{isRunning(current) ? t("act.elapsed", { d: durationText((now - new Date(current.started_at || current.created_at).getTime()) / 1000, lang) }) : durationText(seconds(current), lang)}</dd>
            <dt>{t("act.taskId")}</dt><dd className="nx-act-id"><span className="nx-mono">{current.id}</span><button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("act.copyId")} title={t("act.copyId")} onClick={copyId}><Copy size={14} aria-hidden="true" /></button></dd>
            {current.cancelled_by && <><dt>{t("act.cancelledBy")}</dt><dd>{current.cancelled_by}</dd></>}
          </dl>
          {current.status === "failed" && current.error && (
            <div className="nx-bn nx-act-err" data-tone="danger" role="note"><TriangleAlert size={16} aria-hidden="true" /><span className="nx-bn-t">{localizeApiText(t, current.error)}</span></div>
          )}
          <TaskLog key={current.id} task={current} onChanged={load} terminal tools />
          {audit.length > 0 && (
            <details className="nx-journal-audit">
              <summary>{t("tl.audit")} ({Math.min(audit.length, 8)})</summary>
              {audit.slice(0, 8).map((l, i) => <p key={i} className="nx-mono nx-muted">{l.timestamp} · {l.action} · {l.result}{l.error_message ? ` · ${l.error_message}` : ""}</p>)}
            </details>
          )}
        </>
      )}
    </section>
  );

  const statusTabs = (
    <div className="nx-seg2" role="group" aria-label={t("task.status")}>
      {[["all", t("act.view.all")], ["running", `${t("act.view.running")} · ${counts.running}`], ["failed", `${t("act.view.failed")} · ${counts.failed}`]].map(([k, label]) => (
        <button key={k} type="button" aria-pressed={view === k} onClick={() => setView(k)}>{label}</button>
      ))}
    </div>
  );
  const summary = rows && t("act.summary", { run: counts.running, fail: counts.failed, period });
  const header = !nodeId && (embedded
    ? <div className="nx-bar">{summary && <span className="nx-muted">{summary}</span>}</div>
    : <PageHeader title={t("act.title")} desc={summary} />);
  const people = accounts(all);

  return (
    <>
      {header}
      {runningSection}
      {attentionSection}
      <div className="nx-bar" role="group" aria-label={t("act.filters")}>
        {statusTabs}
        <label className="nx-search2"><Search size={15} aria-hidden="true" /><input type="search" aria-label={t("act.target")} value={f.target} onChange={upd("target")} placeholder={t("act.searchPh")} /></label>
        <select className="nx-sel" aria-label={t("act.type")} value={f.type} onChange={upd("type")}><option value="">{t("act.allTypes")}</option>{Object.keys(TASK_LABEL_KEYS).map((k) => <option key={k} value={k}>{taskLabel(k)}</option>)}</select>
        {!nodeId && nodes.length > 1 && <select className="nx-sel" aria-label={t("ns.node")} value={f.node} onChange={upd("node")}><option value="">{t("act.allNodes")}</option>{nodes.map((n) => <option key={n.id} value={n.id === "local" ? n.name : n.id}>{n.name}</option>)}</select>}
        <select className="nx-sel" aria-label={t("act.period")} value={f.since} onChange={upd("since")}>{Object.keys(SINCE).map((k) => <option key={k} value={k}>{t(`act.since.${k}`)}</option>)}</select>
        {dirty && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => { setF(EMPTY_F); setView("all"); }}>{t("act.clear")}</button>}
        <span className="nx-sp" />
        <button type="button" className="nx-btn" disabled={!rows?.length || exporting} onClick={download}><Download size={15} aria-hidden="true" />{t("act.export")}</button>
      </div>
      {people.length > 1 && (
        <div className="nx-act-people" role="group" aria-label={t("act.accounts")}>
          <span className="nx-muted">{t("act.accounts")}</span>
          {people.map(({ username, count }) => {
            const off = hidden.has(username);
            return (
              <button key={username} type="button" className="nx-act-chip" aria-pressed={!off} title={t(off ? "act.showAccount" : "act.hideAccount", { user: username })} onClick={() => flipAccount(username)}>
                {off ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}{username} <span className="nx-muted">{count}</span>
              </button>
            );
          })}
          {me && people.some((p) => p.username === me) && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={onlyMine}>{t("act.onlyMine")}</button>}
          {hidden.size > 0 && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={showAccounts}>{t("act.showAll")}</button>}
        </div>
      )}
      {unreachable.length > 0 && <p className="nx-notice nx-notice--warning" role="status" style={{ margin: "0 0 var(--space-2)" }}>{t("act.unreachable", { nodes: unreachable.join(", ") })}</p>}
      <div className="nx-act-layout">
        <div className="nx-act-main">{history}</div>
        {rows != null && !error && journal}
      </div>
    </>
  );
}
ActivityPage.ownHeader = true;
