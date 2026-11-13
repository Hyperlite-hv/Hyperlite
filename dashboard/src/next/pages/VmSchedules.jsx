import { useCallback, useEffect, useState } from "react";
import { CalendarClock, Pencil, Plus, Trash2 } from "lucide-react";
import { createVmSchedule, deleteVmSchedule, fetchVmSchedules, updateVmSchedule } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { Empty, Field, SideDrawer, TableWrap } from "../components/ui";
import { Cmd } from "../components/portal";
import { InlineError } from "../components/States";

const ACTIONS = ["start", "shutdown", "stop", "snapshot"];
const WEEK = [0, 1, 2, 3, 4, 5, 6];
const EMPTY = { action: "shutdown", days: [0, 1, 2, 3, 4], time: "20:00", keep: 7, enabled: true };
const asList = (r) => (Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : []);

// "Mon–Fri", "Every day", "Mon, Wed" from weekday numbers (0 = Monday).
export function daysText(days, t) {
  const d = [...days].sort();
  if (d.length === 7) return t("sch.everyDay");
  if (d.join() === "0,1,2,3,4") return t("sch.weekdays");
  if (d.join() === "5,6") return t("sch.weekend");
  return d.map((x) => t(`sch.d.${x}`)).join(", ");
}

function ScheduleDrawer({ vm, open, schedule, onClose, onSaved }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => { if (open) { setForm(schedule ? { ...EMPTY, ...schedule } : EMPTY); setError(null); } }, [open, schedule]);
  const toggleDay = (d) => setForm((f) => ({ ...f, days: f.days.includes(d) ? f.days.filter((x) => x !== d) : [...f.days, d] }));
  const invalid = !form.days.length ? t("sch.e.days") : !/^([01]\d|2[0-3]):[0-5]\d$/.test(form.time) ? t("sch.e.time") : null;
  async function save() {
    if (invalid) return;
    setBusy(true); setError(null);
    const body = { action: form.action, days: form.days, time: form.time, keep: Number(form.keep), enabled: form.enabled };
    try {
      await (schedule ? updateVmSchedule(vm.name, schedule.id, body) : createVmSchedule(vm.name, body));
      pushToast({ kind: "success", title: schedule ? t("sch.saved") : t("sch.created"), message: vm.name });
      onSaved(); onClose();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return (
    <SideDrawer open={open} title={schedule ? t("sch.edit") : t("sch.create")} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={busy || !!invalid} onClick={save}>{schedule ? t("sch.save") : t("sch.create")}</button>
    </>}>
      {error && <InlineError message={error} />}
      <Field label={t("sch.action")}>{(p) => <select {...p} className="nx-inp" value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })}>{ACTIONS.map((a) => <option key={a} value={a}>{t(`sch.a.${a}`)}</option>)}</select>}</Field>
      <fieldset className="hc-sch-days">
        <legend>{t("sch.days")}</legend>
        <div className="nx-seg">{WEEK.map((d) => <button key={d} type="button" aria-pressed={form.days.includes(d)} onClick={() => toggleDay(d)}>{t(`sch.d.${d}`)}</button>)}</div>
        <div className="hc-sch-quick">
          <button type="button" className="nx-lnk" onClick={() => setForm({ ...form, days: [0, 1, 2, 3, 4] })}>{t("sch.weekdays")}</button>
          <button type="button" className="nx-lnk" onClick={() => setForm({ ...form, days: [...WEEK] })}>{t("sch.everyDay")}</button>
        </div>
      </fieldset>
      <Field label={t("sch.time")} hint={t("sch.timeHelp")} error={form.time && invalid === t("sch.e.time") ? invalid : null}>{(p) => <input {...p} className="nx-inp" type="time" value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} />}</Field>
      {form.action === "snapshot" && <Field label={t("sch.keep")} hint={t("sch.keepHelp")}>{(p) => <input {...p} className="nx-inp" type="number" min={1} max={100} value={form.keep} onChange={(e) => setForm({ ...form, keep: Math.max(1, Math.min(100, Number(e.target.value) || 1)) })} />}</Field>}
      {form.action === "stop" && <p className="nx-hint">{t("sch.stopHelp")}</p>}
      <label className="nx-check"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />{t("sch.enabled")}</label>
    </SideDrawer>
  );
}

// VM ▸ Schedules: start, shut down, stop or snapshot the VM at set times, run by the node that hosts it.
export default function VmSchedulesPage({ resource: vm }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [drawer, setDrawer] = useState(null);
  const load = useCallback(() => {
    fetchVmSchedules(vm.name).then((r) => { setItems(asList(r)); setError(null); }).catch((e) => { setError(errorMessage(e)); setItems((x) => x || []); });
  }, [vm.name]);
  useEffect(() => { load(); }, [load]);
  async function remove(s) {
    if (!(await confirmAction({ title: t("sch.deleteTitle"), message: t("sch.deleteMsg", { what: t(`sch.a.${s.action}`), days: daysText(s.days, t), time: s.time }), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    try { await deleteVmSchedule(vm.name, s.id); load(); } catch (e) { pushToast({ kind: "error", title: t("sch.deleteFailed"), message: errorMessage(e) }); }
  }
  return (
    <>
      <div className="hc-cmd" role="toolbar" aria-label={t("actions")}><Cmd icon={Plus} label={t("sch.create")} onClick={() => setDrawer({ schedule: null })} /></div>
      <p className="nx-ph-desc">{t("sch.desc")}</p>
      {error && <InlineError message={error} onRetry={load} />}
      {items == null ? <p className="nx-muted">{t("loading")}</p> : items.length === 0 ? (
        <Empty icon={CalendarClock} title={t("sch.none")} text={t("sch.noneHelp")} action={<button type="button" className="nx-btn nx-btn--primary" onClick={() => setDrawer({ schedule: null })}>{t("sch.create")}</button>} />
      ) : (
        <TableWrap>
          <table className="nx-table hc-grid--list">
            <thead><tr><th scope="col">{t("sch.action")}</th><th scope="col">{t("sch.when")}</th><th scope="col">{t("sch.next")}</th><th scope="col">{t("sch.last")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
            <tbody>{items.map((s) => (
              <tr key={s.id}>
                <th scope="row">{t(`sch.a.${s.action}`)}{s.action === "snapshot" && <small className="nx-muted"> · {t("sch.keeps", { n: s.keep })}</small>}{!s.enabled && <small className="nx-muted"> · {t("sch.off")}</small>}</th>
                <td>{daysText(s.days, t)} · {s.time}</td>
                <td>{s.enabled && s.next_run ? formatDateTime(s.next_run, lang) : "—"}</td>
                <td>{s.last_run ? <span className="hc-sch-last" data-ok={s.last_result === "succeeded" || undefined} title={s.last_result || undefined}>{formatDateTime(s.last_run, lang)}{s.last_result !== "succeeded" && <> · {t("sch.failed")}</>}</span> : <span className="nx-muted">{t("sch.never")}</span>}</td>
                <td><div className="nx-ra">
                  <button type="button" className="hc-ibtn" aria-label={t("sch.editOf", { what: t(`sch.a.${s.action}`), time: s.time })} title={t("sch.edit")} onClick={() => setDrawer({ schedule: s })}><Pencil size={15} aria-hidden="true" /></button>
                  <button type="button" className="hc-ibtn" aria-label={t("sch.deleteOf", { what: t(`sch.a.${s.action}`), time: s.time })} title={t("menu.delete").replace("…", "")} onClick={() => remove(s)}><Trash2 size={15} aria-hidden="true" /></button>
                </div></td>
              </tr>
            ))}</tbody>
          </table>
        </TableWrap>
      )}
      <ScheduleDrawer vm={vm} open={!!drawer} schedule={drawer?.schedule || null} onClose={() => setDrawer(null)} onSaved={load} />
    </>
  );
}
