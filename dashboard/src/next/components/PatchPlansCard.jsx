import { useCallback, useEffect, useState } from "react";
import { CalendarRange, PencilLine, Plus, Trash2 } from "lucide-react";
import { deletePatchPlan, fetchPatchPlans, fetchPools, savePatchPlan } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { Card, Chip, Empty, Field, SideDrawer, TableWrap } from "./ui";

const DAYS = [0, 1, 2, 3, 4, 5, 6];
const browserTz = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; } };
const EMPTY = { name: "", target_kind: "pool", target: "", vms: [], days: [1], monthday: null, rhythm: "week", at_time: "03:00", tz: browserTz(), duration_min: 120, reboot: true, enabled: true };

// Patch plans (docs/design/console-2.md, section 4): recurring windows; at each start the running VMs of the plan get a
// guest update run, followed like one made by hand.
export default function PatchPlansCard({ vmNames }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [plans, setPlans] = useState(null);
  const [pools, setPools] = useState([]);
  const [edit, setEdit] = useState(null);
  const load = useCallback(() => fetchPatchPlans().then((p) => setPlans(p || [])).catch(() => setPlans([])), []);
  useEffect(() => { load(); fetchPools().then((p) => setPools(p || [])).catch(() => {}); }, [load]);
  const fail = (e) => pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) });
  const dayName = (d) => new Intl.DateTimeFormat(lang, { weekday: "short" }).format(new Date(Date.UTC(2024, 0, 1 + d)));
  const when = (p) => (p.days.length ? p.days.map(dayName).join(", ") : p.monthday === -1 ? t("pp.lastDay") : t("pp.monthday", { n: p.monthday })) + ` · ${p.at_time} (${p.tz})`;
  const target = (p) => (p.target_kind === "vms" ? p.target.join(", ") : p.target_kind === "pool" ? `${t("acc.kind.pool")} ${pools.find((x) => String(x.id) === p.target)?.name || p.target}` : `${t("pp.tag")} ${p.target}`);
  async function save(f) {
    const body = { name: f.name.trim(), target_kind: f.target_kind, target: f.target_kind === "vms" ? f.vms : f.target, days: f.rhythm === "week" ? f.days : [], monthday: f.rhythm === "month" ? Number(f.monthday) : null, at_time: f.at_time, tz: f.tz, duration_min: Number(f.duration_min), reboot: f.reboot, enabled: f.enabled };
    try { await savePatchPlan(body, f.id); setEdit(null); load(); pushToast({ kind: "success", title: t("pp.saved"), message: body.name }); } catch (e) { fail(e); }
  }
  async function remove(p) {
    if (!(await confirmAction({ title: t("pp.deleteTitle", { name: p.name }), message: t("pp.deleteMsg"), confirmLabel: t("pp.delete"), danger: true }))) return;
    try { await deletePatchPlan(p.id); load(); } catch (e) { fail(e); }
  }
  return (
    <Card title={t("pp.title")} note={t("pp.note")} flush actions={<button type="button" className="nx-btn nx-btn--sm" onClick={() => setEdit({ ...EMPTY })}><Plus size={14} aria-hidden="true" /> {t("pp.new")}</button>}>
      {!plans ? <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4)" }}>{t("loading")}</p> : !plans.length ? <Empty icon={CalendarRange} title={t("pp.none")} text={t("pp.noneText")} /> : (
        <TableWrap label={t("pp.title")}>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("pp.name")}</th><th scope="col">{t("pp.on")}</th><th scope="col">{t("pp.when")}</th><th scope="col">{t("pp.next")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>
              {plans.map((p) => (
                <tr key={p.id}>
                  <th scope="row">{p.name}{!p.enabled && <> <Chip>{t("pp.off")}</Chip></>}{p.reboot && <> <Chip>{t("gu.withReboot")}</Chip></>}</th>
                  <td>{target(p)}</td>
                  <td>{when(p)} · {t("pp.length", { n: p.duration_min })}</td>
                  <td className="nx-mono">{p.enabled && p.next?.[0] ? formatDateTime(p.next[0], lang) : "—"}</td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("pp.edit", { name: p.name })} onClick={() => setEdit({ ...EMPTY, ...p, vms: p.target_kind === "vms" ? p.target : [], target: p.target_kind === "vms" ? "" : p.target, rhythm: p.days.length ? "week" : "month", days: p.days.length ? p.days : [1] })}><PencilLine size={15} aria-hidden="true" /></button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" aria-label={t("pp.deleteTitle", { name: p.name })} onClick={() => remove(p)}><Trash2 size={15} aria-hidden="true" /></button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {edit && <PlanDrawer t={t} plan={edit} pools={pools} vmNames={vmNames} dayName={dayName} onClose={() => setEdit(null)} onSave={save} />}
    </Card>
  );
}

function PlanDrawer({ t, plan, pools, vmNames, dayName, onClose, onSave }) {
  const [f, setF] = useState(plan);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const toggleDay = (d) => setF((x) => ({ ...x, days: x.days.includes(d) ? x.days.filter((y) => y !== d) : [...x.days, d].sort() }));
  const toggleVm = (n) => setF((x) => ({ ...x, vms: x.vms.includes(n) ? x.vms.filter((y) => y !== n) : [...x.vms, n] }));
  const valid = f.name.trim() && (f.target_kind === "vms" ? f.vms.length : String(f.target).trim()) && (f.rhythm === "week" ? f.days.length : f.monthday);
  return (
    <SideDrawer open title={plan.id ? t("pp.editTitle", { name: plan.name }) : t("pp.new")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!valid} onClick={() => onSave(f)}>{t("pp.save")}</button>
    </>}>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("pp.help")}</p>
      <Field label={t("pp.name")}>{(p) => <input {...p} className="nx-inp" maxLength={100} value={f.name} onChange={set("name")} placeholder={t("pp.namePh")} />}</Field>
      <Field label={t("pp.on")}>{(p) => <select {...p} className="nx-inp" value={f.target_kind} onChange={set("target_kind")}><option value="pool">{t("acc.kind.pool")}</option><option value="tag">{t("pp.tag")}</option><option value="vms">{t("pp.someVms")}</option></select>}</Field>
      {f.target_kind === "pool" && <Field label={t("acc.kind.pool")}>{(p) => <select {...p} className="nx-inp" value={f.target} onChange={set("target")}><option value="">—</option>{pools.map((x) => <option key={x.id} value={String(x.id)}>{x.name}</option>)}</select>}</Field>}
      {f.target_kind === "tag" && <Field label={t("pp.tag")}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.target} onChange={set("target")} placeholder="prod" />}</Field>}
      {f.target_kind === "vms" && <fieldset className="nx-fs"><legend>{t("pp.someVms")}</legend>{vmNames.map((n) => <label key={n} className="nx-check"><input type="checkbox" checked={f.vms.includes(n)} onChange={() => toggleVm(n)} /> <span className="nx-mono">{n}</span></label>)}</fieldset>}
      <Field label={t("pp.rhythm")}>{(p) => <select {...p} className="nx-inp" value={f.rhythm} onChange={set("rhythm")}><option value="week">{t("pp.weekly")}</option><option value="month">{t("pp.monthly")}</option></select>}</Field>
      {f.rhythm === "week" ? (
        <fieldset className="nx-fs hc-days"><legend>{t("pp.days")}</legend>{DAYS.map((d) => <label key={d} className="nx-check"><input type="checkbox" checked={f.days.includes(d)} onChange={() => toggleDay(d)} /> {dayName(d)}</label>)}</fieldset>
      ) : (
        <Field label={t("pp.dayOfMonth")}>{(p) => <select {...p} className="nx-inp" value={f.monthday || ""} onChange={set("monthday")}><option value="">—</option>{Array.from({ length: 28 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}</option>)}<option value={-1}>{t("pp.lastDay")}</option></select>}</Field>
      )}
      <div className="nx-fg">
        <Field label={t("pp.at")}>{(p) => <input {...p} type="time" className="nx-inp" value={f.at_time} onChange={set("at_time")} />}</Field>
        <Field label={t("pp.length1")} unit="min">{(p) => <input {...p} type="number" min={30} max={1440} step={30} className="nx-inp" value={f.duration_min} onChange={set("duration_min")} />}</Field>
      </div>
      <Field label={t("pp.tz")}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.tz} onChange={set("tz")} />}</Field>
      <label className="nx-check"><input type="checkbox" checked={f.reboot} onChange={set("reboot")} /> {t("gu.reboot")}</label>
      <label className="nx-check"><input type="checkbox" checked={f.enabled} onChange={set("enabled")} /> {t("pp.enabled")}</label>
    </SideDrawer>
  );
}
