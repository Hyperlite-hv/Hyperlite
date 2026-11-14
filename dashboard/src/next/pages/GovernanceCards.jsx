import { useCallback, useEffect, useState } from "react";
import { createExclusion, decideCampaignItem, deleteCampaign, deleteExclusion, fetchCampaignItems, fetchCampaigns, fetchExclusions, fetchPrivileges, saveCampaign } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { localizePrivileges } from "../lib/apiText";
import { Card, Chip, Field, SideDrawer, TableWrap } from "../components/ui";
import { PerimeterPicker } from "./AccessPage";

const where = (t, r) => (r.resource_type === "cluster" ? t("acc.wholeCluster") : `${t(`acc.kind.${r.resource_type}`)} ${r.resource_id}`);

// Exclusions (docs/design/governance.md, section 6): a privilege taken from an account or a group on a perimeter and
// below it, whatever the assignments give. Administrators only.
export function ExclusionsCard({ subjects, pools, nodes }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [privileges, setPrivileges] = useState({});
  const [adding, setAdding] = useState(false);
  const load = useCallback(() => Promise.all([fetchExclusions(), fetchPrivileges()]).then(([r, p]) => { setRows(r); setPrivileges(localizePrivileges(t, p)); }).catch(() => setRows(null)), [t]);
  useEffect(() => { load(); }, [load]);
  if (!rows) return null;
  const subject = (e) => (e.subject_type === "group" ? `${t("sec.group")} ${subjects.groups.find((g) => String(g.id) === e.subject_id)?.name || e.subject_id}` : e.subject_id);
  return (
    <Card title={t("exc.title")} note={t("exc.note")} flush actions={<button type="button" className="nx-btn nx-btn--primary" onClick={() => setAdding(true)}>{t("exc.add")}</button>}>
      {rows.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("exc.none")}</p> : (
        <TableWrap label={t("exc.title")}>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("sec.subject")}</th><th scope="col">{t("exc.what")}</th><th scope="col">{t("sec.on")}</th><th scope="col">{t("gr.reason")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <th scope="row">{subject(e)}</th>
                  <td><Chip tone="danger">{e.privilege === "*" ? t("exc.all") : privileges[e.privilege] || e.privilege}</Chip></td>
                  <td>{where(t, e)}</td>
                  <td>{e.reason || "—"}</td>
                  <td><div className="nx-ra"><button type="button" className="nx-btn nx-btn--ghost" onClick={async () => { if (await confirmAction({ title: t("exc.removeTitle"), message: subject(e), confirmLabel: t("sec.remove") })) { await deleteExclusion(e.id).catch((x) => pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(x) })); load(); } }}>{t("sec.remove")}</button></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {adding && <ExclusionDrawer t={t} subjects={subjects} pools={pools} nodes={nodes} privileges={privileges} onClose={() => setAdding(false)} onDone={() => { setAdding(false); load(); pushToast({ kind: "success", title: t("exc.added") }); }} />}
    </Card>
  );
}

function ExclusionDrawer({ t, subjects, pools, nodes, privileges, onClose, onDone }) {
  const [f, setF] = useState({ subject_type: "user", subject_id: "", privilege: "*", reason: "" });
  const [at, setAt] = useState({ resource_type: "vm", resource_id: "" });
  const [error, setError] = useState(null);
  const list = f.subject_type === "user" ? subjects.users.map((u) => [u.username, u.username]) : subjects.groups.map((g) => [String(g.id), g.name]);
  async function submit() { try { await createExclusion({ ...f, ...at }); onDone(); } catch (e) { setError(errorMessage(e)); } }
  return (
    <SideDrawer open title={t("exc.add")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.subject_id || !at.resource_id} onClick={submit}>{t("exc.add")}</button>
    </>}>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("exc.help")}</p>
      <div className="nx-fg">
        <Field label={t("sec.who")}>{(p) => <select {...p} className="nx-inp" value={f.subject_type} onChange={(e) => setF((x) => ({ ...x, subject_type: e.target.value, subject_id: "" }))}><option value="user">{t("sec.user")}</option><option value="group">{t("sec.group")}</option></select>}</Field>
        <Field label={t("sec.subject")}>{(p) => <select {...p} className="nx-inp" value={f.subject_id} onChange={(e) => setF((x) => ({ ...x, subject_id: e.target.value }))}><option value="">{t("sec.choose")}</option>{list.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
      </div>
      <Field label={t("exc.what")}>{(p) => <select {...p} className="nx-inp" value={f.privilege} onChange={(e) => setF((x) => ({ ...x, privilege: e.target.value }))}><option value="*">{t("exc.all")}</option>{Object.entries(privileges).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>}</Field>
      <PerimeterPicker value={at} onChange={setAt} pools={pools} nodes={nodes} />
      <Field label={t("gr.reason")}>{(p) => <input {...p} className="nx-inp" maxLength={500} value={f.reason} onChange={(e) => setF((x) => ({ ...x, reason: e.target.value }))} />}</Field>
      {error && <p className="nx-err" role="alert">{error}</p>}
    </SideDrawer>
  );
}

// Scheduled access reviews (docs/design/governance.md, section 5): a round every N days, reviewers who keep or remove
// each assignment to review, and what nobody decided removed at the deadline when the review says so.
export function CampaignsCard({ admin, users }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rows, setRows] = useState(null);
  const [open, setOpen] = useState(null);
  const [items, setItems] = useState([]);
  const [editing, setEditing] = useState(null);
  const load = useCallback(() => fetchCampaigns().then(setRows).catch(() => setRows(null)), []);
  useEffect(() => { load(); }, [load]);
  const loadItems = useCallback((id) => fetchCampaignItems(id).then(setItems).catch(() => setItems([])), []);
  useEffect(() => { if (open) loadItems(open); }, [open, loadItems]);
  if (!rows || (!admin && rows.length === 0)) return null;
  async function decide(c, i, decision) {
    try { await decideCampaignItem(c, i.id, decision); loadItems(c); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("rc.title")} note={t("rc.note")} flush actions={admin && <button type="button" className="nx-btn nx-btn--primary" onClick={() => setEditing({ name: "", every_days: 90, deadline_days: 14, reviewers: [], remove_undecided: false })}>{t("rc.new")}</button>}>
      {rows.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("rc.none")}</p> : (
        <TableWrap label={t("rc.title")}>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("pl.name")}</th><th scope="col">{t("rc.every")}</th><th scope="col">{t("rc.reviewers")}</th><th scope="col">{t("rc.round")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <th scope="row">{c.name}</th>
                  <td>{t("rc.everyN", { n: c.every_days })}{c.remove_undecided && <> <Chip tone="warning">{t("rc.autoRemove")}</Chip></>}</td>
                  <td>{c.reviewers.join(", ") || t("rc.admins")}</td>
                  <td>{c.round_due_at ? <Chip tone="accent">{t("rc.openUntil", { d: formatDateTime(c.round_due_at, lang) })}</Chip> : <span className="nx-muted">{t("rc.nextOn", { d: formatDateTime(c.next_at, lang) })}</span>}</td>
                  <td><div className="nx-ra">
                    {c.round_due_at && <button type="button" className="nx-btn" onClick={() => setOpen(open === c.id ? null : c.id)}>{t("rc.review")}</button>}
                    {admin && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setEditing({ ...c })}>{t("gr.edit")}</button>}
                    {admin && <button type="button" className="nx-btn nx-btn--ghost" onClick={async () => { if (await confirmAction({ title: t("rc.deleteTitle"), message: c.name, confirmLabel: t("sec.remove"), danger: true })) { await deleteCampaign(c.id); load(); } }}>{t("sec.remove")}</button>}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {open && (
        <TableWrap label={t("rc.review")}>
          <table className="nx-table">
            <tbody>
              {items.length === 0 && <tr><td className="nx-muted">{t("rc.nothing")}</td></tr>}
              {items.map((i) => (
                <tr key={i.id}>
                  <th scope="row">{i.summary}</th>
                  <td>{i.decision ? <Chip tone={i.decision === "keep" ? "success" : "danger"}>{t(`rc.decision.${i.decision}`)}</Chip> : <div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--ghost" onClick={() => decide(open, i, "keep")}>{t("rv.keep")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost" onClick={() => decide(open, i, "remove")}>{t("sec.remove")}</button>
                  </div>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {editing && <CampaignDrawer t={t} c={editing} users={users} onClose={() => setEditing(null)} onDone={() => { setEditing(null); load(); pushToast({ kind: "success", title: t("gr.saved") }); }} />}
    </Card>
  );
}

function CampaignDrawer({ t, c, users, onClose, onDone }) {
  const [f, setF] = useState({ ...c, reviewers: c.reviewers || [] });
  const [error, setError] = useState(null);
  async function submit() {
    try { await saveCampaign({ name: f.name, every_days: Number(f.every_days), deadline_days: Number(f.deadline_days), reviewers: f.reviewers, remove_undecided: f.remove_undecided }, c.id || null); onDone(); } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <SideDrawer open title={c.id ? c.name : t("rc.new")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.name.trim()} onClick={submit}>{t("gr.save")}</button>
    </>}>
      <Field label={t("pl.name")}>{(p) => <input {...p} className="nx-inp" maxLength={100} value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} />}</Field>
      <div className="nx-fg">
        <Field label={t("rc.every")} hint={t("rc.inDays")}>{(p) => <input {...p} type="number" min="1" max="366" className="nx-inp" value={f.every_days} onChange={(e) => setF((x) => ({ ...x, every_days: e.target.value }))} />}</Field>
        <Field label={t("rc.deadline")} hint={t("rc.inDays")}>{(p) => <input {...p} type="number" min="1" max="90" className="nx-inp" value={f.deadline_days} onChange={(e) => setF((x) => ({ ...x, deadline_days: e.target.value }))} />}</Field>
      </div>
      <fieldset className="nx-fs"><legend>{t("rc.reviewers")}</legend>
        <div className="nx-checks">{users.map((u) => <label key={u} className="nx-check"><input type="checkbox" checked={f.reviewers.includes(u)} onChange={() => setF((x) => ({ ...x, reviewers: x.reviewers.includes(u) ? x.reviewers.filter((y) => y !== u) : [...x.reviewers, u] }))} /> {u}</label>)}</div>
        <p className="nx-muted">{t("rc.reviewersHint")}</p>
      </fieldset>
      <label className="nx-check"><input type="checkbox" checked={f.remove_undecided} onChange={(e) => setF((x) => ({ ...x, remove_undecided: e.target.checked }))} /> {t("rc.removeUndecided")}</label>
      {error && <p className="nx-err" role="alert">{error}</p>}
    </SideDrawer>
  );
}
