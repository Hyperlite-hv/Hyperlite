import { useCallback, useEffect, useState } from "react";
import { fetchAuditLog, fetchAuditActions, fetchAuditCount, downloadAuditCsv } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { errorMessage } from "../lib/errors";
import StatusIndicator from "../components/StatusIndicator";
import { ErrorState } from "../components/States";
import { PageHeader, Empty, Loading, TableWrap } from "../components/ui";
import { Download, ScrollText } from "lucide-react";

const iso = (local) => (local ? new Date(local).toISOString() : undefined);
const LIMIT = 300;
const EMPTY_F = { result: "", action: "", username: "", resource: "", from: "", to: "" };

// Audit journal (GET /audit, administrators): every filter of the API — result, action, user, resource,
// from AND to (`jusqu_a`, never exposed before) — with typing debounce, live refresh and CSV export.
export default function JournalPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [rows, setRows] = useState(null);
  const [actions, setActions] = useState([]);
  const [error, setError] = useState(null);
  const [f, setF] = useState(EMPTY_F);
  const [total, setTotal] = useState(null);
  const [applied, setApplied] = useState(f);
  const [open, setOpen] = useState(null);
  const [exporting, setExporting] = useState(false);
  const pushToast = useInfraStore((s) => s.pushToast);
  const filters = (a) => ({ result: a.result, action: a.action, username: a.username, resource: a.resource, depuis: iso(a.from), jusqu_a: iso(a.to) });

  useEffect(() => { const h = setTimeout(() => setApplied(f), 300); return () => clearTimeout(h); }, [f]); // debounce typing
  useEffect(() => { fetchAuditActions().then((a) => setActions(Array.isArray(a) ? a : [])).catch(() => {}); }, []);

  const load = useCallback(async () => {
    try {
      const r = await fetchAuditLog({ limit: LIMIT, ...filters(applied) });
      setRows(Array.isArray(r) ? r : []); setError(null);
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
  return (
    <>
      <PageHeader title={t("tab.journal")} count={total ?? (rows ? rows.length : null)} desc={t("jr.desc")} />
      <div className="nx-bar" role="group" aria-label={t("jr.filters")}>
        <select className="nx-sel" aria-label={t("a11y.filter_by_result")} value={f.result} onChange={upd("result")}><option value="">{t("jr.allResults")}</option><option value="succes">{t("jr.success")}</option><option value="echec">{t("jr.failure")}</option></select>
        <select className="nx-sel" aria-label={t("a11y.filter_by_action_type")} value={f.action} onChange={upd("action")}><option value="">{t("jr.allActions")}</option>{actions.map((a) => <option key={a} value={a}>{a}</option>)}</select>
        <input className="nx-inp" style={{ width: "10rem", height: "2.1333rem" }} aria-label={t("a11y.user")} placeholder={t("task.user")} type="search" value={f.username} onChange={upd("username")} />
        <input className="nx-inp" style={{ width: "11.3333rem", height: "2.1333rem" }} aria-label={t("a11y.target_resource")} placeholder={t("jr.resource")} type="search" value={f.resource} onChange={upd("resource")} />
        <label className="nx-bar-lbl">{t("jr.from")}<input className="nx-inp nx-mono" style={{ width: "auto", height: "2.1333rem" }} aria-label={t("a11y.show_entries_from")} type="datetime-local" value={f.from} onChange={upd("from")} /></label>
        <label className="nx-bar-lbl">{t("jr.to")}<input className="nx-inp nx-mono" style={{ width: "auto", height: "2.1333rem" }} aria-label={t("a11y.show_entries_until")} type="datetime-local" value={f.to} onChange={upd("to")} /></label>
        {dirty && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setF(EMPTY_F)}>{t("act.clear")}</button>}
        <span className="nx-sp" />
        <button type="button" className="nx-btn" disabled={!rows?.length || exporting} title={total != null ? t("act.exportAll", { n: total }) : undefined} onClick={download}><Download size={15} aria-hidden="true" />{t("act.export")}</button>
      </div>
      {rows && total != null && total > rows.length && <p className="nx-hint" role="status" style={{ margin: "0 0 var(--space-2)" }}>{t("jr.partial", { n: new Intl.NumberFormat(lang).format(rows.length), total: new Intl.NumberFormat(lang).format(total) })}</p>}
      <div className="nx-card2 nx-card2--flush">
        {error ? <ErrorState message={error} onRetry={load} />
          : rows == null ? <Loading style={{ padding: "var(--space-4)" }} />
          : rows.length === 0 ? <Empty icon={ScrollText} title={t("jr.none")} text={dirty ? t("act.noneFiltered") : null} /> : (
            <TableWrap>
              <table className="nx-table">
                <thead><tr><th scope="col">{t("jr.time")}</th><th scope="col">{t("jr.result")}</th><th scope="col">{t("task.user")}</th><th scope="col">{t("jr.action")}</th><th scope="col">{t("jr.resource")}</th><th scope="col">{t("jr.ip")}</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className={r.error_message ? "nx-rowlink" : undefined} onClick={() => r.error_message && setOpen(open === r.id ? null : r.id)}>
                      <td className="nx-mono">{fmt(r.timestamp)}</td>
                      <td><StatusIndicator override={r.result === "succes" ? { key: "jr.success", shape: "check", tone: "success" } : { key: "jr.failure", shape: "cross", tone: "danger" }} /></td>
                      <td>{r.username || "—"}</td>
                      <td className="nx-mono">{r.action}</td>
                      <td className="nx-mono" style={{ whiteSpace: open === r.id ? "normal" : undefined, overflowWrap: "anywhere" }}>{r.resource || "—"}{r.error_message && <span className="nx-tone-danger"> — {open === r.id ? r.error_message : `${r.error_message.slice(0, 70)}${r.error_message.length > 70 ? "…" : ""}`}</span>}</td>
                      <td className="nx-mono nx-muted">{r.ip || "—"}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot><tr><td colSpan={6}>{t("jr.footer", { total: total ?? rows.length, shown: rows.length })}</td></tr></tfoot>
              </table>
            </TableWrap>
          )}
      </div>
    </>
  );
}
JournalPage.ownHeader = true;
