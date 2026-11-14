import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarClock, RefreshCw } from "lucide-react";
import { cancelGuestUpdates, fetchGuestUpdates, scanGuestUpdates, scheduleGuestUpdates } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState } from "../components/States";
import { Card, Chip, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";

const local = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

// Guest updates (docs/design/governance.md, section 11): what each running guest has pending, through its agent, and
// runs that install it in a window, followed until each guest says it is done.
export default function GuestUpdatesPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const vms = useInfraStore((s) => s.vms);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [picked, setPicked] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [planning, setPlanning] = useState(null);
  const load = useCallback(() => fetchGuestUpdates().then((d) => { setData(d); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id); }, [load]);
  const running = useMemo(() => vms.filter((v) => v.node === "local" && v.state === "active").map((v) => v.name).sort(), [vms]);
  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Loading />;
  const state = Object.fromEntries(data.states.map((s) => [s.vm_name, s]));
  const names = [...new Set([...running, ...data.states.map((s) => s.vm_name)])].sort();
  const toggle = (n) => setPicked((p) => { const x = new Set(p); if (x.has(n)) x.delete(n); else x.add(n); return x; });
  async function scan() {
    setBusy(true);
    try { await scanGuestUpdates([...picked]); pushToast({ kind: "success", title: t("gu.scanned") }); load(); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  return (
    <>
      <PageHeader title={t("tab.guestupdates")} desc={t("gu.desc")} actions={<>
        <button type="button" className="nx-btn" disabled={!picked.size || busy} onClick={scan}><RefreshCw size={14} aria-hidden="true" /> {t("gu.scan", { n: picked.size })}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={!picked.size} onClick={() => { const now = new Date(); setPlanning({ start: local(now), end: local(new Date(now.getTime() + 2 * 3600000)), reboot: false }); }}><CalendarClock size={14} aria-hidden="true" /> {t("gu.plan")}</button>
      </>} />
      <Card flush>
        <TableWrap label={t("tab.guestupdates")}>
          <table className="nx-table">
            <thead><tr><th scope="col" className="hc-pick"><input type="checkbox" aria-label={t("bulkc.all")} checked={names.length > 0 && picked.size === names.length} onChange={(e) => setPicked(new Set(e.target.checked ? names : []))} /></th><th scope="col">VM</th><th scope="col">{t("gu.pending")}</th><th scope="col">{t("gu.checked")}</th></tr></thead>
            <tbody>
              {names.map((n) => {
                const s = state[n];
                return (
                  <tr key={n}>
                    <td className="hc-pick"><input type="checkbox" aria-label={t("bulkc.pick", { name: n })} checked={picked.has(n)} onChange={() => toggle(n)} /></td>
                    <th scope="row" className="nx-mono">{n}</th>
                    <td>{!s ? <span className="nx-muted">{t("gu.never")}</span> : s.error ? <Chip tone="danger">{s.error}</Chip> : <>{s.pending ? <Chip tone="warning">{t("gu.n", { n: s.pending })}</Chip> : <Chip tone="success">{t("gu.upToDate")}</Chip>}{s.security ? <> <Chip tone="danger">{t("gu.security", { n: s.security })}</Chip></> : null} <small className="nx-muted">{s.family}</small></>}</td>
                    <td>{s ? formatDateTime(s.checked_at, lang) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableWrap>
      </Card>
      <Card title={t("gu.runs")} flush>
        {data.runs.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("gu.noRun")}</p> : (
          <TableWrap label={t("gu.runs")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("gu.window")}</th><th scope="col">VM</th><th scope="col">{t("ac.status")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {data.runs.map((r) => (
                  <tr key={r.id}>
                    <th scope="row">{formatDateTime(r.window_start, lang)} → {formatDateTime(r.window_end, lang)}{r.reboot && <> <Chip>{t("gu.withReboot")}</Chip></>}</th>
                    <td>{r.vms.map((v) => <div key={v}><span className="nx-mono">{v}</span> {r.results[v] && <Chip tone={r.results[v].state === "done" ? "success" : r.results[v].state === "running" ? "accent" : "danger"}>{t(`gu.st.${r.results[v].state}`)}</Chip>}{r.results[v]?.error && <small className="nx-muted"> {r.results[v].error}</small>}</div>)}</td>
                    <td><Chip tone={r.status === "done" ? "success" : r.status === "cancelled" ? undefined : "accent"}>{t(`gu.run.${r.status}`)}</Chip></td>
                    <td><div className="nx-ra">{r.status === "scheduled" && <button type="button" className="nx-btn nx-btn--ghost" onClick={async () => { await cancelGuestUpdates(r.id).catch(() => {}); load(); }}>{t("action.cancel")}</button>}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      {planning && (
        <SideDrawer open title={t("gu.plan")} onClose={() => setPlanning(null)} footer={<>
          <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setPlanning(null)}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--primary" onClick={async () => {
            try { await scheduleGuestUpdates({ vms: [...picked], window_start: new Date(planning.start).toISOString(), window_end: new Date(planning.end).toISOString(), reboot: planning.reboot }); setPlanning(null); setPicked(new Set()); pushToast({ kind: "success", title: t("gu.planned") }); load(); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
          }}>{t("gu.plan")}</button>
        </>}>
          <p className="nx-muted" style={{ marginTop: 0 }}>{t("gu.planHelp", { n: picked.size })}</p>
          <div className="nx-fg">
            <Field label={t("gu.start")}>{(p) => <input {...p} type="datetime-local" className="nx-inp" value={planning.start} onChange={(e) => setPlanning((x) => ({ ...x, start: e.target.value }))} />}</Field>
            <Field label={t("gu.end")}>{(p) => <input {...p} type="datetime-local" className="nx-inp" value={planning.end} onChange={(e) => setPlanning((x) => ({ ...x, end: e.target.value }))} />}</Field>
          </div>
          <label className="nx-check"><input type="checkbox" checked={planning.reboot} onChange={(e) => setPlanning((x) => ({ ...x, reboot: e.target.checked }))} /> {t("gu.reboot")}</label>
        </SideDrawer>
      )}
    </>
  );
}
GuestUpdatesPage.ownHeader = true;
