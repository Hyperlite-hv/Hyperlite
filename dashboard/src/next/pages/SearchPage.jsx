import { useCallback, useEffect, useMemo, useState } from "react";
import { Bookmark, Pin, Search, Trash2 } from "lucide-react";
import { deleteSavedQuery, fetchSavedQueries, saveQuery, updateSavedQuery } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { runQuery, useQueryStore } from "../lib/query";
import { openObject, useQueryObjects } from "../lib/queryObjects";
import { TagChips } from "../components/NotesCard";
import { PinDialog } from "../components/PinDialog";
import { TileBody } from "../components/DashboardTile";
import { Card, Chip, Empty, Field, PageHeader, SideDrawer, TableWrap } from "../components/ui";

const EXAMPLES = ["kind:vm state:stopped", "tag:prod -pool:web", "vcpu>=4 mem>8", "kind:vm os:windows", "name:web-*"];
const VIEWS = ["list", "count", "bars"];
const GROUPS = ["state", "node", "os", "pool", "tag", "kind"];

// Search by query (docs/design/governance.md, section 14): every VM, container and node, narrowed by terms such as
// kind:vm state:stopped tag:prod pool:web vcpu>=4 mem>8 (app: lib/query.js). A query is saved with a name and a view
// (docs/design/console-2.md, section 3), opened again from here and pinned to a dashboard.
export default function SearchPage() {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const pushToast = useInfraStore((s) => s.pushToast);
  const q = useQueryStore((s) => s.q);
  const setQ = useQueryStore((s) => s.setQ);
  const objects = useQueryObjects();
  const { found, errors } = useMemo(() => runQuery(objects, q), [objects, q]);
  const [saved, setSaved] = useState([]);
  const [current, setCurrent] = useState(null); // the saved query being shown, when one was opened
  const [saving, setSaving] = useState(false);
  const [pinning, setPinning] = useState(null);
  const load = useCallback(() => fetchSavedQueries().then((x) => setSaved(Array.isArray(x) ? x : [])).catch(() => setSaved([])), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (current && current.query !== q) setCurrent(null); }, [q, current]);
  const fail = (e) => pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) });
  async function remove(s) {
    if (!(await confirmAction({ title: t("sq.deleteTitle", { name: s.name }), message: t("sq.deleteMsg"), confirmLabel: t("sq.delete"), danger: true }))) return;
    try { await deleteSavedQuery(s.id); setCurrent(null); load(); } catch (e) { fail(e); }
  }
  const tileOf = (s) => ({ type: "query", title: s.name, size: 1, config: { query: s.query, view: s.view, group_by: s.group_by } });
  return (
    <>
      <PageHeader title={t("tab.search")} desc={t("qs.desc")} actions={<>
        <button type="button" className="nx-btn nx-btn--sm" disabled={!q.trim() || errors.length > 0} onClick={() => setSaving(true)}><Bookmark size={14} aria-hidden="true" /> {current?.editable ? t("sq.update") : t("sq.save")}</button>
        <button type="button" className="nx-btn nx-btn--sm" disabled={!q.trim() || errors.length > 0} onClick={() => setPinning(current ? tileOf(current) : { type: "query", title: q.trim().slice(0, 120), size: 1, config: { query: q.trim(), view: "list" } })}><Pin size={14} aria-hidden="true" /> {t("pd.action")}</button>
      </>} />
      {saved.length > 0 && (
        <div className="hc-sq" role="group" aria-label={t("sq.saved")}>
          <span className="nx-muted">{t("sq.saved")}</span>
          {saved.map((s) => (
            <span key={s.id} className="hc-sq-item" data-on={current?.id === s.id || undefined}>
              <button type="button" className="nx-lnk" title={s.query} onClick={() => { setQ(s.query); setCurrent(s); }}>{s.name}</button>
              {s.shared !== "private" && <small className="nx-muted">{t("dsh.sharedShort")}</small>}
              {s.editable && <button type="button" className="hc-ibtn" aria-label={t("sq.deleteTitle", { name: s.name })} onClick={() => remove(s)}><Trash2 size={13} aria-hidden="true" /></button>}
            </span>
          ))}
        </div>
      )}
      <label className="hc-filter hc-filter--wide"><Search size={16} aria-hidden="true" /><input type="search" className="nx-mono" value={q} onChange={(e) => setQ(e.target.value)} placeholder="kind:vm state:stopped tag:prod" aria-label={t("tab.search")} autoFocus /></label>
      <p className="nx-muted" style={{ fontSize: "var(--fs-13)" }}>{t("qs.help")} {EXAMPLES.map((ex) => <button key={ex} type="button" className="nx-lnk nx-mono" style={{ marginRight: "var(--space-2)" }} onClick={() => setQ(ex)}>{ex}</button>)}</p>
      {errors.length > 0 && <p className="nx-err" role="alert">{t("qs.unknown", { terms: errors.join(", ") })}</p>}
      {current && current.view !== "list" && q.trim() && !errors.length && <Card title={current.name}><TileBody tile={tileOf(current)} /></Card>}
      <Card flush>
        {!q.trim() ? <Empty icon={Search} title={t("qs.start")} text={t("qs.startText")} /> : found.length === 0 ? <Empty icon={Search} title={t("qs.none")} text="" /> : (
          <TableWrap label={t("tab.search")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("pl.name")}</th><th scope="col">{t("qs.kind")}</th><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("acc.node")}</th><th scope="col">{t("pl.members")}</th><th scope="col">{t("pl.tags")}</th></tr></thead>
              <tbody>
                {found.map((o) => (
                  <tr key={`${o.kind}:${o.node}:${o.name}`}>
                    <th scope="row"><button type="button" className="nx-lnk nx-mono" onClick={() => openObject(navigateTo, o)}>{o.name}</button></th>
                    <td><Chip>{t(`qs.k.${o.kind}`)}</Chip></td>
                    <td>{o.state}</td>
                    <td>{o.nodeName}</td>
                    <td>{o.pools.join(", ") || "—"}</td>
                    <td><TagChips tags={o.tags} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <p className="hc-count" role="status">{q.trim() ? t("qs.count", { n: found.length }) : null}</p>
      <SaveQuery open={saving} onClose={() => setSaving(false)} query={q.trim()} current={current?.editable ? current : null}
        onSaved={(s) => { setSaving(false); setCurrent(s); load(); pushToast({ kind: "success", title: t("sq.saved1"), message: s.name }); }} onFail={fail} />
      <PinDialog open={!!pinning} onClose={() => setPinning(null)} tile={pinning} />
    </>
  );
}
SearchPage.ownHeader = true;

function SaveQuery({ open, onClose, query, current, onSaved, onFail }) {
  const t = useT();
  const [f, setF] = useState({ name: "", view: "list", group_by: "state", shared: "private" });
  useEffect(() => { if (open) setF(current ? { name: current.name, view: current.view, group_by: current.group_by || "state", shared: current.shared } : { name: "", view: "list", group_by: "state", shared: "private" }); }, [open, current]);
  async function go() {
    const body = { name: f.name.trim(), query, view: f.view, group_by: f.view === "bars" ? f.group_by : null, shared: f.shared };
    try { onSaved(current ? await updateSavedQuery(current.id, body) : await saveQuery(body)); } catch (e) { onFail(e); }
  }
  return (
    <SideDrawer open={open} title={current ? t("sq.updateTitle", { name: current.name }) : t("sq.saveTitle")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.name.trim()} onClick={go}>{t("dsh.save")}</button>
    </>}>
      <p className="nx-mono nx-muted" style={{ marginTop: 0 }}>{query}</p>
      <Field label={t("dsh.name")}>{(p) => <input {...p} className="nx-inp" maxLength={80} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus />}</Field>
      <Field label={t("sq.view")}>{(p) => <select {...p} className="nx-inp" value={f.view} onChange={(e) => setF({ ...f, view: e.target.value })}>{VIEWS.map((v) => <option key={v} value={v}>{t(`sq.v.${v}`)}</option>)}</select>}</Field>
      {f.view === "bars" && <Field label={t("sq.groupBy")}>{(p) => <select {...p} className="nx-inp" value={f.group_by} onChange={(e) => setF({ ...f, group_by: e.target.value })}>{GROUPS.map((g) => <option key={g} value={g}>{t(`sq.by.${g}`)}</option>)}</select>}</Field>}
      <Field label={t("dsh.sharedWith")}>{(p) => <select {...p} className="nx-inp" value={f.shared.startsWith("group:") ? f.shared : f.shared} onChange={(e) => setF({ ...f, shared: e.target.value })}>
        <option value="private">{t("dsh.private")}</option><option value="everyone">{t("dsh.everyone")}</option>
        {f.shared.startsWith("group:") && <option value={f.shared}>{f.shared}</option>}
      </select>}</Field>
    </SideDrawer>
  );
}
