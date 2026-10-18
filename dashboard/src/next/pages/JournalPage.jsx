import { useCallback, useEffect, useState } from "react";
import { fetchAuditLog, fetchAuditActions, fetchAuditCount, downloadAuditCsv, fetchRetention, saveRetention } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { listOf } from "../lib/clusterList";
import { errorMessage } from "../lib/errors";
import StatusIndicator from "../components/StatusIndicator";
import { ErrorState } from "../components/States";
import { PageHeader, Empty, Loading, TableWrap, SideDrawer, Field } from "../components/ui";
import { Download, ScrollText, Timer } from "lucide-react";

const iso = (local) => (local ? new Date(local).toISOString() : undefined);
const LIMIT = 300;
const EMPTY_F = { result: "", action: "", username: "", resource: "", from: "", to: "" };

// Audit journal (GET /audit, administrators): every filter of the API — result, action, user, resource,
// from AND to (`until`, never exposed before) — with typing debounce, live refresh and CSV export.
// embedded: the Audit log tab of the Activity page (ActivityHub.jsx), under its header.
export default function JournalPage({ embedded = false }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [rows, setRows] = useState(null);
  const [unreachable, setUnreachable] = useState([]);
  const [actions, setActions] = useState([]);
  const [error, setError] = useState(null);
  const [f, setF] = useState(EMPTY_F);
  const [total, setTotal] = useState(null);
  const [applied, setApplied] = useState(f);
  const [open, setOpen] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [retentionOpen, setRetentionOpen] = useState(false);
  const pushToast = useInfraStore((s) => s.pushToast);
  const filters = (a) => ({ result: a.result, action: a.action, username: a.username, resource: a.resource, since: iso(a.from), until: iso(a.to) });

  useEffect(() => { const h = setTimeout(() => setApplied(f), 300); return () => clearTimeout(h); }, [f]); // debounce typing
  useEffect(() => { fetchAuditActions().then((a) => setActions(Array.isArray(a) ? a : [])).catch(() => {}); }, []);

  const load = useCallback(async () => {
    try {
      // In a cluster, every member's entries, each with the node that holds it, and the members that did not answer.
      const r = await fetchAuditLog({ limit: LIMIT, ...filters(applied) });
      setRows(listOf(r)); setUnreachable(Array.isArray(r?.unreachable) ? r.unreachable : []); setError(null);
      fetchAuditCount(filters(applied)).then((c) => setTotal(c?.total ?? null)).catch(() => setTotal(null));
    } catch (e) { setError(errorMessage(e)); }
  }, [applied]);
  useEffect(() => { load(); }, [load]);
  usePolling(load, 15000);

  const upd = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const fmt = (ts) => new Intl.DateTimeFormat(lang, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(ts));
  // The whole filtered log, from the server: the page itself only holds the most recent entries.
  const download = async () => {
    setExporting(true);
    try { await downloadAuditCsv(filters(applied)); }
    catch (e) { pushToast({ kind: "error", title: t("act.exportFailed"), message: errorMessage(e) }); }
    finally { setExporting(false); }
  };

  const dirty = JSON.stringify(f) !== JSON.stringify(EMPTY_F);
  // A node column once the entries come from more than one node (a cluster).
  const several = unreachable.length > 0 || new Set((rows || []).map((r) => r.on)).size > 1;
  return (
    <>
      {embedded
        ? <p className="nx-muted" style={{ margin: 0 }}>{t("jr.desc")}{total != null ? ` · ${t("jr.count", { n: total.toLocaleString() })}` : ""}</p>
        : <PageHeader title={t("tab.journal")} count={total ?? (rows ? rows.length : null)} desc={t("jr.desc")} />}
      <div className="nx-bar" role="group" aria-label={t("jr.filters")}>
        <select className="nx-sel" aria-label={t("a11y.filter_by_result")} value={f.result} onChange={upd("result")}><option value="">{t("jr.allResults")}</option><option value="success">{t("jr.success")}</option><option value="failed">{t("jr.failure")}</option></select>
        <select className="nx-sel" aria-label={t("a11y.filter_by_action_type")} value={f.action} onChange={upd("action")}><option value="">{t("jr.allActions")}</option>{actions.map((a) => <option key={a} value={a}>{a}</option>)}</select>
        <input className="nx-inp" style={{ width: "10rem", height: "2.1333rem" }} aria-label={t("a11y.user")} placeholder={t("task.user")} type="search" value={f.username} onChange={upd("username")} />
        <input className="nx-inp" style={{ width: "11.3333rem", height: "2.1333rem" }} aria-label={t("a11y.target_resource")} placeholder={t("jr.resource")} type="search" value={f.resource} onChange={upd("resource")} />
        <label className="nx-bar-lbl">{t("jr.from")}<input className="nx-inp nx-mono" style={{ width: "auto", height: "2.1333rem" }} aria-label={t("a11y.show_entries_from")} type="datetime-local" value={f.from} onChange={upd("from")} /></label>
        <label className="nx-bar-lbl">{t("jr.to")}<input className="nx-inp nx-mono" style={{ width: "auto", height: "2.1333rem" }} aria-label={t("a11y.show_entries_until")} type="datetime-local" value={f.to} onChange={upd("to")} /></label>
        {dirty && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setF(EMPTY_F)}>{t("act.clear")}</button>}
        <span className="nx-sp" />
        <button type="button" className="nx-btn" onClick={() => setRetentionOpen(true)}><Timer size={15} aria-hidden="true" />{t("ret.open")}</button>
        <button type="button" className="nx-btn" disabled={!rows?.length || exporting} title={total != null ? t("act.exportAll", { n: total }) : undefined} onClick={download}><Download size={15} aria-hidden="true" />{t("act.export")}</button>
      </div>
      <RetentionDrawer open={retentionOpen} onClose={() => setRetentionOpen(false)} />
      {rows && total != null && total > rows.length && <p className="nx-hint" role="status" style={{ margin: "0 0 var(--space-2)" }}>{t("jr.partial", { n: new Intl.NumberFormat(lang).format(rows.length), total: new Intl.NumberFormat(lang).format(total) })}</p>}
      {unreachable.length > 0 && <p className="nx-notice nx-notice--warning" role="status" style={{ margin: "0 0 var(--space-2)" }}>{t("jr.unreachable", { nodes: unreachable.join(", ") })}</p>}
      <div className="nx-card2 nx-card2--flush">
        {error ? <ErrorState message={error} onRetry={load} />
          : rows == null ? <Loading style={{ padding: "var(--space-4)" }} />
          : rows.length === 0 ? <Empty icon={ScrollText} title={t("jr.none")} text={dirty ? t("act.noneFiltered") : null} /> : (
            <TableWrap>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("jr.time")}</th>{several && <th scope="col">{t("ns.node")}</th>}<th scope="col">{t("jr.result")}</th><th scope="col">{t("task.user")}</th><th scope="col">{t("jr.action")}</th><th scope="col">{t("jr.resource")}</th><th scope="col">{t("jr.ip")}</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.on}/${r.id}`} className={r.error_message ? "nx-rowlink" : undefined} onClick={() => r.error_message && setOpen(open === `${r.on}/${r.id}` ? null : `${r.on}/${r.id}`)}>
                      <td className="nx-mono">{fmt(r.timestamp)}</td>
                      {several && <td>{r.on}</td>}
                      <td><StatusIndicator override={r.result === "success" ? { key: "jr.success", shape: "check", tone: "success" } : { key: "jr.failure", shape: "cross", tone: "danger" }} /></td>
                      <td>{r.username || "—"}</td>
                      <td className="nx-mono">{r.action}</td>
                      <td className="nx-mono" style={{ whiteSpace: open === `${r.on}/${r.id}` ? "normal" : undefined, overflowWrap: "anywhere" }}>{r.resource || "—"}{r.error_message && <span className="nx-tone-danger"> — {open === `${r.on}/${r.id}` ? r.error_message : `${r.error_message.slice(0, 70)}${r.error_message.length > 70 ? "…" : ""}`}</span>}</td>
                      <td className="nx-mono nx-muted">{r.ip || "—"}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot><tr><td colSpan={several ? 7 : 6}>{t("jr.footer", { total: total ?? rows.length, shown: rows.length })}</td></tr></tfoot>
              </table>
            </TableWrap>
          )}
      </div>
    </>
  );
}
JournalPage.ownHeader = true;

const MAX_DAYS = 3650;
const valid = (v, min) => /^\d+$/.test(String(v)) && Number(v) <= MAX_DAYS && (Number(v) === 0 || Number(v) >= min);

// How long the users' addresses are kept (GDPR): the access log's daily files (0: none) and the audit log
// (0: for ever). The cluster's settings, applied by every node (app/core/access_log.py, app/core/audit.py).
function RetentionDrawer({ open, onClose }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [f, setF] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!open) return;
    setF(null); setError(null);
    fetchRetention().then((r) => setF({ access: String(r.access_log_days), audit: String(r.audit_days), dir: r.access_log_dir })).catch((e) => setError(errorMessage(e)));
  }, [open]);
  const ok = f && valid(f.access, 1) && valid(f.audit, 1);
  const save = async () => {
    setBusy(true);
    try {
      await saveRetention({ access_log_days: Number(f.access), audit_days: Number(f.audit) });
      pushToast({ kind: "success", title: t("ret.saved") });
      onClose();
    } catch (e) { pushToast({ kind: "error", title: t("ret.failed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  };
  return (
    <SideDrawer open={open} title={t("ret.title")} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!ok || busy} onClick={save}>{t("ret.save")}</button>
    </>}>
      <p className="nx-muted" style={{ margin: 0 }}>{t("ret.intro")}</p>
      {error ? <ErrorState message={error} /> : !f ? <Loading /> : <>
        <Field label={t("ret.access")} unit={t("ret.days")} hint={t("ret.accessHint", { dir: f.dir })} error={valid(f.access, 1) ? null : t("ret.rule")}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min="0" max={MAX_DAYS} value={f.access} onChange={(e) => setF({ ...f, access: e.target.value })} />}</Field>
        <Field label={t("ret.audit")} unit={t("ret.days")} hint={t("ret.auditHint")} error={valid(f.audit, 1) ? null : t("ret.rule")}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min="0" max={MAX_DAYS} value={f.audit} onChange={(e) => setF({ ...f, audit: e.target.value })} />}</Field>
      </>}
    </SideDrawer>
  );
}
