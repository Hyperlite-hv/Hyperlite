import { useCallback, useEffect, useState } from "react";
import { DoorOpen, Pencil, Plus, Trash2 } from "lucide-react";
import { deleteSigninRule, fetchGroups, fetchSigninRules, saveSigninRule } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";
import { Card, Chip, Empty, Field, Loading, PageHeader, SideDrawer, TableWrap } from "../components/ui";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const blank = { name: "", subject_type: "all", subject_id: "", networks: "", require_2fa: false, days: [], hours: "", mode: "watch" };

// Sign-in rules (docs/design/governance.md, section 4): where from, on which days and hours, and with which factor an
// account may sign in and use the API. "Watch" journals what a rule would refuse, to try it before enforcing it.
export default function SigninRulesPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [rules, setRules] = useState(null);
  const [groups, setGroups] = useState([]);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);
  const load = useCallback(async () => {
    try { const [r, g] = await Promise.all([fetchSigninRules(), fetchGroups().catch(() => [])]); setRules(r); setGroups(g); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  if (error && !rules) return <ErrorState message={error} onRetry={load} />;
  if (!rules) return <Loading />;
  const who = (r) => (r.subject_type === "all" ? t("sr.everyone") : r.subject_type === "role" ? t(`sr.role.${r.subject_id}`) : r.subject_type === "group" ? `${t("sec.group")} ${groups.find((g) => String(g.id) === r.subject_id)?.name || r.subject_id}` : r.subject_id);
  const conditions = (r) => [r.networks.length && r.networks.join(", "), r.days.length && r.days.map((d) => t(`sr.day.${d}`)).join(" "), r.hours, r.require_2fa && t("sr.2fa")].filter(Boolean).join(" · ");
  async function remove(r) {
    if (!(await confirmAction({ title: t("sr.deleteTitle"), message: r.name, confirmLabel: t("sec.remove"), danger: true }))) return;
    try { await deleteSigninRule(r.id); load(); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  return (
    <>
      <PageHeader title={t("tab.signin")} desc={t("sr.desc")} actions={<button type="button" className="nx-btn nx-btn--primary" onClick={() => setEditing(blank)}><Plus size={14} aria-hidden="true" /> {t("sr.new")}</button>} />
      <Card flush>
        {rules.length === 0 ? <Empty icon={DoorOpen} title={t("sr.none")} text={t("sr.noneText")} /> : (
          <TableWrap label={t("tab.signin")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("pl.name")}</th><th scope="col">{t("sr.for")}</th><th scope="col">{t("sr.conditions")}</th><th scope="col">{t("sr.mode")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id}>
                    <th scope="row">{r.name}</th>
                    <td>{who(r)}</td>
                    <td className="nx-mono">{conditions(r)}</td>
                    <td><Chip tone={r.mode === "enforce" ? "danger" : "warning"}>{t(`sr.mode.${r.mode}`)}</Chip></td>
                    <td><div className="nx-ra">
                      <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("gr.edit")} onClick={() => setEditing({ ...r, networks: r.networks.join(", "), hours: r.hours || "", subject_id: r.subject_id || "" })}><Pencil size={14} aria-hidden="true" /></button>
                      <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("sec.remove")} onClick={() => remove(r)}><Trash2 size={14} aria-hidden="true" /></button>
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <p className="nx-muted" style={{ fontSize: "var(--fs-13)" }}>{t("sr.rescue")}</p>
      {editing && <RuleDrawer t={t} rule={editing} groups={groups} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); pushToast({ kind: "success", title: t("gr.saved") }); }} />}
    </>
  );
}
SigninRulesPage.ownHeader = true;

function RuleDrawer({ t, rule, groups, onClose, onSaved }) {
  const [f, setF] = useState(rule);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const toggleDay = (d) => setF((x) => ({ ...x, days: x.days.includes(d) ? x.days.filter((y) => y !== d) : [...x.days, d] }));
  async function submit() {
    try {
      await saveSigninRule({ ...f, networks: f.networks.split(",").map((x) => x.trim()).filter(Boolean), hours: f.hours.trim() || null, subject_id: f.subject_type === "all" ? null : f.subject_id }, rule.id || null);
      onSaved();
    } catch (e) { setError(errorMessage(e)); }
  }
  return (
    <SideDrawer open title={rule.id ? rule.name : t("sr.new")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!f.name.trim()} onClick={submit}>{t("gr.save")}</button>
    </>}>
      <Field label={t("pl.name")}>{(p) => <input {...p} className="nx-inp" maxLength={100} value={f.name} onChange={set("name")} />}</Field>
      <div className="nx-fg">
        <Field label={t("sr.for")}>{(p) => <select {...p} className="nx-inp" value={f.subject_type} onChange={(e) => setF((x) => ({ ...x, subject_type: e.target.value, subject_id: e.target.value === "role" ? "admin" : "" }))}>{["all", "role", "group", "user"].map((k) => <option key={k} value={k}>{t(`sr.kind.${k}`)}</option>)}</select>}</Field>
        {f.subject_type === "role" && <Field label={t("sec.role")}>{(p) => <select {...p} className="nx-inp" value={f.subject_id} onChange={set("subject_id")}><option value="admin">{t("sr.role.admin")}</option><option value="viewer">{t("sr.role.viewer")}</option><option value="delegated">{t("sr.role.delegated")}</option></select>}</Field>}
        {f.subject_type === "group" && <Field label={t("sec.group")}>{(p) => <select {...p} className="nx-inp" value={f.subject_id} onChange={set("subject_id")}><option value="">{t("sec.choose")}</option>{groups.map((g) => <option key={g.id} value={String(g.id)}>{g.name}</option>)}</select>}</Field>}
        {f.subject_type === "user" && <Field label={t("sec.user")}>{(p) => <input {...p} className="nx-inp" value={f.subject_id} onChange={set("subject_id")} />}</Field>}
      </div>
      <Field label={t("sr.networks")} hint={t("sr.networksHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.networks} onChange={set("networks")} placeholder="10.0.0.0/8, 192.168.1.0/24" />}</Field>
      <fieldset className="nx-fs"><legend>{t("sr.days")}</legend>
        <div className="hc-seg">{DAYS.map((d) => <button key={d} type="button" className="nx-btn nx-btn--ghost" aria-pressed={f.days.includes(d)} onClick={() => toggleDay(d)}>{t(`sr.day.${d}`)}</button>)}</div>
      </fieldset>
      <Field label={t("sr.hours")} hint={t("sr.hoursHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.hours} onChange={set("hours")} placeholder="08:00-19:00" />}</Field>
      <label className="nx-check"><input type="checkbox" checked={f.require_2fa} onChange={set("require_2fa")} /> {t("sr.2faLabel")}</label>
      <Field label={t("sr.mode")} hint={t("sr.modeHint")}>{(p) => <select {...p} className="nx-inp" value={f.mode} onChange={set("mode")}><option value="watch">{t("sr.mode.watch")}</option><option value="enforce">{t("sr.mode.enforce")}</option></select>}</Field>
      {error && <p className="nx-err" role="alert">{error}</p>}
    </SideDrawer>
  );
}
