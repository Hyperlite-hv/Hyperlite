import { useCallback, useEffect, useState } from "react";
import { fetchAuditLog, fetchAuditActions } from "../../api/client";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { errorMessage } from "../lib/errors";
import StatusIndicator from "../components/StatusIndicator";
import { ErrorState } from "../components/States";

function csv(rows) {
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  return ["timestamp,user,action,resource,result,error", ...rows.map((r) => [r.timestamp, r.username, r.action, r.resource, r.result, r.error_message].map(esc).join(","))].join("\n");
}
const iso = (local) => (local ? new Date(local).toISOString() : undefined);

// Audit journal (GET /audit, administrators): every filter of the API — result, action, user, resource,
// from AND to (`jusqu_a`, never exposed before) — with typing debounce, live refresh and CSV export.
export default function JournalPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [rows, setRows] = useState(null);
  const [actions, setActions] = useState([]);
  const [error, setError] = useState(null);
  const [f, setF] = useState({ result: "", action: "", username: "", resource: "", from: "", to: "" });
  const [applied, setApplied] = useState(f);
  const [open, setOpen] = useState(null);

  useEffect(() => { const h = setTimeout(() => setApplied(f), 300); return () => clearTimeout(h); }, [f]); // debounce typing
  useEffect(() => { fetchAuditActions().then((a) => setActions(Array.isArray(a) ? a : [])).catch(() => {}); }, []);

  const load = useCallback(async () => {
    try {
      const r = await fetchAuditLog({ limit: 300, result: applied.result, action: applied.action, username: applied.username, resource: applied.resource, depuis: iso(applied.from), jusqu_a: iso(applied.to) });
      setRows(Array.isArray(r) ? r : []); setError(null);
    } catch (e) { setError(errorMessage(e)); }
  }, [applied]);
  useEffect(() => { load(); }, [load]);
  usePolling(load, 15000);

  const upd = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const fmt = (ts) => new Intl.DateTimeFormat(lang, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(ts));
  const download = () => {
    const url = URL.createObjectURL(new Blob([csv(rows || [])], { type: "text/csv" }));
    const a = document.createElement("a"); a.href = url; a.download = "hyperlite-audit.csv"; a.click(); URL.revokeObjectURL(url);
  };

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="jr-h">
        <div className="nx-cardhead">
          <h2 id="jr-h">{t("jr.entries")} <span className="nx-count">{rows ? rows.length : "…"}</span></h2>
          <button type="button" className="nx-btn" onClick={load}>{t("top.refresh")}</button>
          <button type="button" className="nx-btn" disabled={!rows?.length} onClick={download}>{t("act.export")}</button>
        </div>
        <div className="nx-filters" role="group" aria-label={t("jr.filters")}>
          <label>{t("jr.result")}<select className="nx-input" aria-label="Filter by result" value={f.result} onChange={upd("result")}><option value="">{t("vmlist.all")}</option><option value="succes">{t("jr.success")}</option><option value="echec">{t("jr.failure")}</option></select></label>
          <label>{t("jr.action")}<select className="nx-input" aria-label="Filter by action type" value={f.action} onChange={upd("action")}><option value="">{t("vmlist.all")}</option>{actions.map((a) => <option key={a} value={a}>{a}</option>)}</select></label>
          <label>{t("task.user")}<input className="nx-input" aria-label="User" type="search" value={f.username} onChange={upd("username")} /></label>
          <label>{t("act.target")}<input className="nx-input" aria-label="Target (resource)" type="search" value={f.resource} onChange={upd("resource")} /></label>
          <label>{t("jr.from")}<input className="nx-input" aria-label="Show entries from" type="datetime-local" value={f.from} onChange={upd("from")} /></label>
          <label>{t("jr.to")}<input className="nx-input" aria-label="Show entries until" type="datetime-local" value={f.to} onChange={upd("to")} /></label>
        </div>
        {error ? <ErrorState message={error} onRetry={load} />
          : rows == null ? <p className="nx-muted">{t("loading")}</p>
          : rows.length === 0 ? <p className="nx-muted" role="status">{t("jr.none")}</p> : (
            <div className="nx-tablewrap">
              <table className="nx-table">
                <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("jr.time")}</th><th scope="col">{t("task.user")}</th><th scope="col">{t("jr.action")}</th><th scope="col">{t("jr.resource")}</th></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className={r.error_message ? "nx-rowlink" : undefined} onClick={() => r.error_message && setOpen(open === r.id ? null : r.id)}>
                      <td><StatusIndicator override={r.result === "succes" ? { key: "jr.success", shape: "check", tone: "success" } : { key: "jr.failure", shape: "cross", tone: "danger" }} /></td>
                      <td className="nx-mono">{fmt(r.timestamp)}</td><td className="nx-mono">{r.username || "—"}</td><td className="nx-mono">{r.action}</td>
                      <td className="nx-mono" style={{ whiteSpace: open === r.id ? "normal" : undefined, overflowWrap: "anywhere" }}>{r.resource || "—"}{r.error_message && <span className="nx-tone-danger"> — {open === r.id ? r.error_message : `${r.error_message.slice(0, 70)}${r.error_message.length > 70 ? "…" : ""}`}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </section>
    </div>
  );
}
